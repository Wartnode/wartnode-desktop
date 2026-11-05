"""Add owned build resources/tests only after verification of upstream sources."""
import shutil


def prepare(root, source, originals):
    node = source / 'src/node'
    shutil.copy2(root / 'native/core_shutdown_regression.cpp', node / 'wartnode-shutdown-regression.cpp')
    shutil.copy2(root / 'native/core.manifest', node / 'wartnode-core.manifest')
    shutil.copy2(root / 'native/core.rc', node / 'wartnode-core.rc')
    (node / 'asyncio/wartnode-conman-before.cpp').write_bytes(originals['src/node/asyncio/conman.cpp'])
    cross = source / 'crosscompile/x86_64-w64-mingw32.txt'
    cross.write_text(cross.read_text().replace("[binaries]\n", "[binaries]\nwindres = 'x86_64-w64-mingw32-windres'\n"))
    meson = node / 'meson.build'
    text = meson.read_text().replace("  './asyncio/conman.cpp',\n", '')
    text = text.replace("executable('wart-node', [src,'./main.cpp'],",
        "windows = import('windows')\ncore_resources = windows.compile_resources('wartnode-core.rc')\n"
        "wartnode_target = executable('wart-node', [src,'./asyncio/conman.cpp','./main.cpp',core_resources],")
    text += '''
common_node_objects = wartnode_target.extract_objects(src)
executable('wartnode-conman-shutdown', ['./wartnode-shutdown-regression.cpp',core_resources],
  objects: [common_node_objects, wartnode_target.extract_objects('./asyncio/conman.cpp')],
  include_directories: ['./', include_thirdparty], link_with: lib_thirdparty,
  dependencies: [sqlite3_dep,libuv_dep,uvw_dep,spdlog_dep])
executable('wartnode-conman-before', ['./wartnode-shutdown-regression.cpp','./asyncio/wartnode-conman-before.cpp',core_resources],
  objects: common_node_objects,
  include_directories: ['./', include_thirdparty], link_with: lib_thirdparty,
  dependencies: [sqlite3_dep,libuv_dep,uvw_dep,spdlog_dep])
'''
    meson.write_text(text)
