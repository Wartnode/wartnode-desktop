// App-owned native regression. No proof, consensus, fee or database rules change.
#include "api/http/endpoint.hpp"
#include "asyncio/conman.hpp"
#include "chainserver/server.hpp"
#include "db/chain_db.hpp"
#include "db/peer_db.hpp"
#include "eventloop/eventloop.hpp"
#include "general/errors.hpp"
#include "global/globals.hpp"
#include <atomic>
#include <chrono>
#include <iostream>
#include <thread>

struct ECCGuard {
    ECCGuard() { ECC_Start(); }
    ~ECCGuard() { ECC_Stop(); }
};

int main(int argc, char** argv)
{
    ECCGuard ecc;
    if (init_config(argc, argv) <= 0) return 10;
    BatchRegistry batches;
    uv_loop_t loop;
    if (uv_loop_init(&loop)) return 11;
    {
        PeerDB peers(config().data.peersdb);
        PeerServer peerServer(peers, config());
        ChainDB database(config().data.chaindb);
        auto chain = ChainServer::make_chain_server(database, batches, config().node.snapshotSigner);
        Conman connections(&loop, peerServer, config());
        HTTPEndpoint endpoint(config().jsonrpc.bind);
        Eventloop events(peerServer, *chain, config());
        global_init(&batches, &peerServer, &*chain, &connections, &events, &endpoint);
        events.start_async_loop();
        bool replied = false;
        connections.async_get_peers([&](auto) { replied = true; });
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (!replied && std::chrono::steady_clock::now() < deadline) {
            uv_run(&loop, UV_RUN_NOWAIT);
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (!replied) return 12; // Healthy wakeups must still dispatch.
        connections.close(ESIGTERM);
        if (uv_run(&loop, UV_RUN_DEFAULT)) return 13;
        // All connection/listener handles are closed. Before the guard this
        // deterministic late producer asserts inside Windows uv_async_send.
        std::atomic<unsigned> lateReplies{0};
        std::thread producers[4];
        for (auto& producer : producers) {
            producer = std::thread([&] {
                for (unsigned i = 0; i < 1000; ++i) {
                    connections.async_get_peers([&](auto) { ++lateReplies; });
                    connections.async_inspect([&](const auto&) { ++lateReplies; });
                }
            });
        }
        for (auto& producer : producers) producer.join();
        if (lateReplies.load()) return 14;
    }
    if (uv_loop_close(&loop)) return 15;
    std::cout << "Healthy callbacks dispatched; 8000 late callbacks rejected after shutdown\n";
    return 0;
}
