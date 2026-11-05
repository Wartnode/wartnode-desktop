"""Exact nonconsensus core patches, shared by bootstrap, build and audit."""
import difflib
import hashlib
import json
import tarfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def patched_sources(originals):
    main = originals['src/node/main.cpp'].decode().replace('\r\n', '\n')
    main = main.replace('    Eventloop el(ps, *cs, config());\n', '', 1).replace(
        '    auto endpointPublic { HTTPEndpoint::make_public_endpoint(config()) };\n',
        '    auto endpointPublic { HTTPEndpoint::make_public_endpoint(config()) };\n\n'
        '    // Join event/chain workers before destroying endpoints used by their callbacks.\n'
        '    Eventloop el(ps, *cs, config());\n', 1)
    cpp = originals['src/node/asyncio/conman.cpp'].decode().replace('\r\n', '\n')
    cpp = cpp.replace('uv_close((uv_handle_t*)&wakeup, close_caller);', 'close_wakeup();')
    assert cpp.count('close_wakeup();') == 2
    position = cpp.index('void Conman::async_send(')
    cpp = cpp[:position] + '''void Conman::close_wakeup()
{
    std::unique_lock<std::mutex> lock(mutex);
    if (wakeupClosing)
        return;
    wakeupClosing = true;
    uv_close((uv_handle_t*)&wakeup, close_caller);
}

''' + cpp[position:]
    for signature in ('void Conman::async_send', 'void Conman::async_delete',
                      'void Conman::async_close', 'void Conman::async_validate'):
        start = cpp.index(signature)
        lock = cpp.index('std::unique_lock<std::mutex> lock(mutex);', start) + len('std::unique_lock<std::mutex> lock(mutex);')
        cpp = cpp[:lock] + '\n    if (wakeupClosing)\n        return;' + cpp[lock:]
    hpp = originals['src/node/asyncio/conman.hpp'].decode().replace('\r\n', '\n')
    hpp = hpp.replace('    void unref(const char*);', '    void unref(const char*);\n    void close_wakeup();')
    hpp = hpp.replace('        events.push(std::move(e));', '        if (wakeupClosing)\n            return;\n        events.push(std::move(e));')
    hpp = hpp.replace('    std::mutex mutex;', '    std::mutex mutex;\n    bool wakeupClosing { false };')
    return {'src/node/main.cpp': main.encode(), 'src/node/asyncio/conman.cpp': cpp.encode(),
            'src/node/asyncio/conman.hpp': hpp.encode()}


def load(root):
    lock = json.loads((root / 'scripts/ci/sources.lock.json').read_text())
    item = next(s for s in lock['sources'] if s['name'] == 'upstream-core')
    archive = root / item['tar_output']
    assert sha(archive.read_bytes()) == item['tar_sha256']
    with tarfile.open(archive) as source:
        originals = {m.name: source.extractfile(m).read() for m in source.getmembers() if m.isfile() or m.issym()}
    patched = patched_sources(originals)
    groups = [('src/node/main.cpp',), ('src/node/asyncio/conman.cpp', 'src/node/asyncio/conman.hpp')]
    assert len(lock['patches']) == len(groups)
    for patch, paths in zip(lock['patches'], groups):
        data = (root / patch['file']).read_bytes()
        assert sha(data) == patch['sha256']
        expected = ''.join(''.join(difflib.unified_diff(
            originals[path].decode().replace('\r\n', '\n').splitlines(True),
            patched[path].decode().splitlines(True), fromfile='a/' + path, tofile='b/' + path)) for path in paths).encode()
        assert data == expected, 'Source transformation differs from locked patch'
    return item, lock['patches'], originals, patched


def verify(root):
    item, patches, originals, patched = load(root)
    base = (root / item['directory']).resolve()
    for name, original in originals.items():
        path = (base / name).resolve()
        assert path.is_relative_to(base)
        assert path.read_bytes() == patched.get(name, original), 'Changed upstream input: ' + name
    return item, patches, originals, patched
