// SPDX-License-Identifier: GPL-3.0-or-later
#include "mining_budget.hpp"
#include <iostream>
using namespace std::chrono;
using brminer::warthog::gpuBudgetDeadline;
int main() {
    const steady_clock::time_point start{};
    for (unsigned limit : {5,20,50,80,95}) {
        const auto active=milliseconds(10);
        const auto end=gpuBudgetDeadline(start,start+active,limit);
        const double ratio=duration<double>(active).count()/duration<double>(end-start).count();
        if (ratio>limit/100.0+1e-6 || ratio<limit/100.0-1e-6) return 1;
        // Waiting on CPU backpressure must not allow a subsequent unpaced burst.
        const auto later=start+seconds(10);
        if (gpuBudgetDeadline(later,later+active,limit)-later!=end-start) return 2;
    }
    for (unsigned limit : {0,4,96,100,1000}) {
        try { gpuBudgetDeadline(start,start+milliseconds(1),limit); return 3; }
        catch (const std::invalid_argument&) {}
    }
    std::cout << "GPU budget limits and no idle-credit burst: passed\n";
}
