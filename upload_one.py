#!/usr/bin/env python3
"""上传一个视频到B站"""
import json
import sys
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from uploader import upload_to_bilibili, verify_credential

# 加载配置
config = yaml.safe_load(Path("config.yaml").read_text())

# 验证凭证
print("🔑 验证B站凭证...")
info = verify_credential(config)
print(f"✅ 登录: {info['name']} (UID: {info['uid']}, Lv{info['level']})")

# 读取 run_info
run_dir = Path("runs/netflix_20260414_115632")
run_info = json.loads((run_dir / "run_info.json").read_text())
pub = run_info["publish_info"]

print(f"\n🎬 准备上传:")
print(f"   标题: {pub['title']}")
print(f"   视频: {pub['video_path']}")
print(f"   封面: {pub['cover_path']}")
print(f"   分区: {pub['tid']}")
print(f"   标签: {pub['tags']}")
print(f"   来源: {pub['source']}")

# 上传
result = upload_to_bilibili(
    config=config,
    video_path=pub["video_path"],
    title=pub["title"],
    desc=pub["description"],
    tags=pub["tags"],
    cover_path=pub["cover_path"],
    source_url=pub["source"],
    tid=pub["tid"],
)

print(f"\n📊 上传结果: {json.dumps(result, ensure_ascii=False, indent=2)}")

# 更新 run_info
if result.get("bvid"):
    run_info["bvid"] = result["bvid"]
    run_info["uploaded"] = True
    (run_dir / "run_info.json").write_text(
        json.dumps(run_info, ensure_ascii=False, indent=2)
    )
    print(f"\n✅ 已更新 run_info.json, bvid={result['bvid']}")
