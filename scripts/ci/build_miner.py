"""Rebuild all consumed miner libraries and the driver-OpenCL entry with clang-cl."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(root, jobs=4):
    clang = shutil.which('clang-cl')
    cmake = shutil.which('cmake')
    ninja = shutil.which('ninja')
    if not all((clang, cmake, ninja, shutil.which('link'), shutil.which('lib'))):
        raise RuntimeError('Use the VS x64 developer environment with clang-cl, CMake and Ninja on PATH')
    source = root / 'vendor/brminer'
    adapter = root / 'work/ci/miner-adapter'
    output = root / 'work/ci/miner-build'
    adapter.mkdir(parents=True, exist_ok=True)
    for folder in ('src', 'cmake'):
        shutil.copytree(source / folder, adapter / folder, dirs_exist_ok=True)
    original = (source / 'CMakeLists.txt').read_text()
    # Reuse the pinned upstream kernel generation and Verus optimization recipes.
    prefix = original.split('add_library(brminer_platform STATIC', 1)[1]
    prefix = 'add_library(brminer_platform STATIC' + prefix.split('add_library(warthog_mining STATIC', 1)[0]
    prefix = prefix.replace('target_include_directories(connectcoin_node PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src")', '')
    native = root / 'native'
    identity = hashlib.sha256((''.join(sha(p) for p in sorted([*native.glob('*.cpp'),*native.glob('*.hpp')]))
                              + sha(root / 'scripts/ci/sources.lock.json') + sha(__file__)).encode()).hexdigest()
    header = f'''cmake_minimum_required(VERSION 3.22)
project(WartNodeMiner LANGUAGES C CXX)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(BUILD_TESTING OFF)
set(connectcoin_source_SOURCE_DIR "{(root / 'vendor/connectcoin').as_posix()}")
set(janusminer_source_SOURCE_DIR "{(root / 'vendor/janusminer').as_posix()}")
'''
    tail = f'''
add_library(connectcoin_crypto STATIC
    "${{connectcoin_source_SOURCE_DIR}}/src/crypto/sha256.cpp"
    "${{connectcoin_source_SOURCE_DIR}}/src/crypto/sha256_sse4.cpp")
target_include_directories(connectcoin_crypto PUBLIC "${{connectcoin_source_SOURCE_DIR}}/src")
add_library(univalue STATIC
    "${{connectcoin_source_SOURCE_DIR}}/src/univalue/lib/univalue.cpp"
    "${{connectcoin_source_SOURCE_DIR}}/src/univalue/lib/univalue_get.cpp"
    "${{connectcoin_source_SOURCE_DIR}}/src/univalue/lib/univalue_read.cpp"
    "${{connectcoin_source_SOURCE_DIR}}/src/univalue/lib/univalue_write.cpp")
target_include_directories(univalue PUBLIC "${{connectcoin_source_SOURCE_DIR}}/src/univalue/include")
add_library(warthog_mining STATIC src/warthog_pow.cpp src/warthog_gpu.cpp src/warthog_console.cpp)
target_include_directories(warthog_mining PUBLIC src "${{connectcoin_source_SOURCE_DIR}}/src"
    "${{janusminer_source_SOURCE_DIR}}/src/shared/src"
    "${{janusminer_source_SOURCE_DIR}}/thirdparty/opencl/include" "${{CMAKE_CURRENT_BINARY_DIR}}/generated")
target_compile_definitions(warthog_mining PRIVATE BRMINER_NATIVE_BATCH=1)
target_link_libraries(warthog_mining PUBLIC brminer_platform connectcoin_crypto janus_verushash univalue warthog_native_batch)
add_executable(wartnode-miner "{native.as_posix()}/miner_main.cpp" "{native.as_posix()}/miner_pipeline.cpp" "{native.as_posix()}/miner_benchmark.cpp")
target_include_directories(wartnode-miner PRIVATE src "${{CMAKE_CURRENT_BINARY_DIR}}/generated")
target_compile_definitions(wartnode-miner PRIVATE BRMINER_NATIVE_BATCH=1 WARTNODE_MINER_BUILD_ID="{identity}")
target_link_libraries(wartnode-miner PRIVATE warthog_mining ws2_32 advapi32)
enable_testing()
add_executable(warthog-pow-tests "{source.as_posix()}/tests/warthog_pow_tests.cpp")
target_link_libraries(warthog-pow-tests PRIVATE warthog_mining ws2_32 advapi32)
add_test(NAME warthog-pow-tests COMMAND warthog-pow-tests)
add_executable(warthog-pow-equivalence-tests "{source.as_posix()}/tests/warthog_pow_equivalence_tests.cpp")
target_link_libraries(warthog-pow-equivalence-tests PRIVATE warthog_mining ws2_32 advapi32)
add_test(NAME warthog-pow-equivalence-tests COMMAND warthog-pow-equivalence-tests)
add_test(NAME warthog-portable-pow-tests COMMAND warthog-pow-tests)
add_test(NAME warthog-portable-equivalence-tests COMMAND warthog-pow-equivalence-tests)
set_tests_properties(warthog-portable-pow-tests warthog-portable-equivalence-tests PROPERTIES ENVIRONMENT "BRMINER_DISABLE_SIMD=1")
add_executable(warthog-native-batch-tests "{source.as_posix()}/tests/warthog_native_batch_tests.cpp")
target_link_libraries(warthog-native-batch-tests PRIVATE warthog_native_batch janus_verushash)
add_test(NAME warthog-native-batch-tests COMMAND warthog-native-batch-tests)
add_executable(mining-budget-tests "{native.as_posix()}/mining_budget_tests.cpp")
add_test(NAME mining-budget-tests COMMAND mining-budget-tests)
'''
    (adapter / 'CMakeLists.txt').write_text(header + prefix + tail)
    subprocess.run([cmake, '-S', str(adapter), '-B', str(output), '-G', 'Ninja',
                    '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_C_COMPILER=' + clang,
                    '-DCMAKE_CXX_COMPILER=' + clang, '-DCMAKE_MAKE_PROGRAM=' + ninja], check=True)
    subprocess.run([cmake, '--build', str(output), '--parallel', str(jobs)], check=True)
    subprocess.run([cmake, '--build', str(output), '--target', 'test'], check=True)
    target = root / 'components/wartnode-miner.exe'
    target.parent.mkdir(exist_ok=True)
    shutil.copy2(output / 'wartnode-miner.exe', target)
    inputs = [{'file': p.relative_to(root).as_posix(), 'sha256': sha(p)} for p in sorted(output.rglob('*.lib'))]
    compiler = subprocess.check_output([clang, '--version'], text=True).splitlines()[0]
    if '23.1.4' not in compiler:
        raise RuntimeError('The locked clang-cl 23.1.4 compiler is required')
    result = {'schema': 1, 'compiler': compiler, 'cpu_batch': 'clang-cl AVX2/AES/PCLMUL/SSSE3 with runtime dispatch',
              'gpu_backend': 'driver OpenCL', 'build_id': identity, 'prebuilt_libraries_used': False,
              'source_lock_sha256': sha(root / 'scripts/ci/sources.lock.json'),
              'adapter_sha256': sha(adapter / 'CMakeLists.txt'), 'libraries': inputs,
              'host_toolchain': {'cmake': subprocess.check_output([cmake, '--version'], text=True).splitlines()[0],
                                 'ninja': subprocess.check_output([ninja, '--version'], text=True).strip(),
                                 'visual_studio': os.environ.get('VisualStudioVersion'),
                                 'msvc_tools': os.environ.get('VCToolsVersion'),
                                 'windows_sdk': os.environ.get('WindowsSDKVersion')},
              'pipeline_sha256': sha(native / 'miner_pipeline.cpp'), 'engine_sha256': sha(target),
              'budget_header_sha256': sha(native / 'mining_budget.hpp'),
              'native_pow_tests_passed': True}
    (root / 'provenance/miner-build.json').write_text(json.dumps(result, indent=2) + '\n')
    (root / 'provenance/miner-link-inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    build(ROOT, args.jobs)
