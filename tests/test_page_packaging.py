import shutil
from pathlib import Path
from types import SimpleNamespace

from astrbot.dashboard.services.plugin_page_service import PluginPageService


async def test_dashboard_discovers_the_shipped_page_and_scoped_bridge(tmp_path):
    root = Path(__file__).resolve().parents[1]
    installed = tmp_path / "plugins" / "word_guardian"
    installed.mkdir(parents=True)
    shutil.copytree(root / "pages", installed / "pages")
    manager = SimpleNamespace(plugin_store_path=str(tmp_path / "plugins"))
    service = PluginPageService(manager)
    metadata = SimpleNamespace(root_dir_name="word_guardian", reserved=False)
    pages = await service.discover_plugin_pages(metadata)
    assert [page.name for page in pages] == ["word-guardian"]
    entry = await service.resolve_plugin_page_file(metadata, "word-guardian", "")
    html = entry.read_text()
    assert "/api/plugin/page/bridge-sdk.js" in html
    assert "./app.js" in html and "./style.css" in html
    assert (entry.parent / "app.js").is_file()
