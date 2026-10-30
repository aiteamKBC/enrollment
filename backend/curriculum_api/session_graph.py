"""Bounded Graph pagination. Transport is injected; this module has no I/O."""
from urllib.parse import urlsplit


def graph_path(value, base_url):
    base = urlsplit(base_url.rstrip('/') + '/')
    target = urlsplit(str(value))
    if target.scheme or target.netloc:
        if (target.scheme, target.netloc) != (base.scheme, base.netloc) or not target.path.startswith(base.path):
            raise RuntimeError('Microsoft returned an invalid pagination link.')
        return target.path[len(base.path):] + ('?' + target.query if target.query else '')
    if str(value).startswith('/') or target.fragment or not value:
        raise RuntimeError('Microsoft returned an invalid pagination link.')
    return str(value)


def collection(get, path, base_url, *, first=None, key='value'):
    """Return a complete collection or raise; never publish a truncated roster."""
    rows, seen = [], set()
    response = first
    for _ in range(200):
        path = graph_path(path, base_url)
        if path in seen:
            raise RuntimeError('Microsoft repeated an attendance page. Please retry.')
        seen.add(path)
        if response is None:
            response = get('GET', path)
        page = response.get(key)
        if not isinstance(page, list):
            raise RuntimeError('Microsoft returned an incomplete collection.')
        rows.extend(page)
        if len(rows) > 100000:
            raise RuntimeError('Microsoft collection exceeds the supported size.')
        next_link = response.get('@odata.nextLink' if key == 'value' else key + '@odata.nextLink')
        if not next_link:
            return rows
        path, response, key = next_link, None, 'value'
    raise RuntimeError('Microsoft collection exceeds the pagination limit.')
