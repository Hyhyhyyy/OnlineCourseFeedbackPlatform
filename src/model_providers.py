"""Server-side inference profiles. Credentials never enter browser responses."""
import os
from urllib.parse import urlsplit


def settings(provider):
    if provider == 'api':
        from api_settings import CONFIG, snapshot
        if CONFIG:
            return snapshot()
    if provider == 'local':
        return dict(model='qwen35-4b', url='http://127.0.0.1:8081/v1', key='')
    if provider not in ('api', 'campus'):
        raise ValueError('未知模型运行方式')
    prefix = 'COURSE_API' if provider == 'api' else 'COURSE_CAMPUS'
    url = os.environ.get(prefix+'_BASE_URL', '').rstrip('/')
    model = os.environ.get(prefix+'_MODEL', '')
    key = os.environ.get(prefix+'_KEY', '')
    if not url or not model:
        raise ValueError('请先在服务器配置 '+prefix+'_BASE_URL 和 '+prefix+'_MODEL')
    parsed=urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not (parsed.scheme=='https' or (parsed.scheme=='http' and parsed.hostname=='127.0.0.1')):
        raise ValueError('请使用不带凭据或查询参数的 HTTPS 地址；本机回环地址可使用 HTTP')
    return dict(model=model, url=url, key=key)
