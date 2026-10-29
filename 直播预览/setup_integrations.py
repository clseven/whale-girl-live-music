"""Install pinned upstream NetEase connector; never change player/account settings."""
import hashlib
import io
import json
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REF = ROOT.parent / '参考项目'

def install():
    catalog = json.loads((REF / 'awoo-connectors/catalog-v2.json').read_text(encoding='utf-8'))
    entry = catalog['connectors']['netease']
    package = entry['package']
    dest = ROOT / 'vendor/netease' / entry['version']
    exe = dest / 'Awoo.Connector.Netease.exe'
    if not exe.exists():
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(package['downloadUrl'], timeout=45) as response:
                raw = response.read(25_000_000)
        except Exception:
            release = json.loads(subprocess.check_output(['gh', 'api', 'repos/Enkianssus/awoo-connectors/releases/tags/netease-v' + entry['version']], timeout=30))
            asset = next(a for a in release['assets'] if a['name'] == package['asset'])
            raw = subprocess.check_output(['gh', 'api', '-H', 'Accept: application/octet-stream', 'repos/Enkianssus/awoo-connectors/releases/assets/' + str(asset['id'])], timeout=90)
        if len(raw) != package['size'] or hashlib.sha256(raw).hexdigest() != package['sha256']:
            raise RuntimeError('连接器下载校验失败')
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for member in archive.infolist():
                target = (dest / member.filename).resolve()
                if not target.is_relative_to(dest.resolve()):
                    raise RuntimeError('连接器压缩包路径无效')
            archive.extractall(dest)
        matches = list(dest.rglob('Awoo.Connector.Netease.exe'))
        if len(matches) != 1:
            raise RuntimeError('连接器入口不唯一')
        exe = matches[0]
    info = {'executable': str(exe.relative_to(ROOT)), 'version': entry['version'],
            'sha256': package['sha256'], 'repository': catalog['repository'],
            'tested_player': entry['testedPlayerVersion']}
    (ROOT / 'vendor/netease/installed.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    hub = REF / 'dyhub'
    result = subprocess.run(['node', str(hub / 'node_modules/typescript/bin/tsc'), '-p', str(hub / 'tsconfig.json')], cwd=hub, capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    import shutil
    shutil.copytree(hub / 'src/ui', hub / 'dist/ui', dirs_exist_ok=True)
    print('Installed NetEase connector', entry['version'], '; DyHub compiled')

if __name__ == '__main__':
    install()
