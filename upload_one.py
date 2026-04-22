#!/usr/bin/env python3
"""
通用B站视频上传脚本 — 自动扫描 runs/ 目录,找到待上传视频一键发布
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

用法:
  # 自动扫描 runs/ 下最新未上传的视频
  python upload_one.py

  # 指定 run 目录上传
  python upload_one.py runs/netflix_20260414_115632

  # 批量上传所有未上传的
  python upload_one.py --all

  # 只列出状态,不上传
  python upload_one.py --list

  # 覆盖标题/分区
  python upload_one.py runs/xxx --title "自定义标题" --tid 183

  # 直接上传视频文件（无需 run 目录）
  python upload_one.py --video output.mp4 --title "标题" --desc "简介" --tags "标签1,标签2" --cover cover.jpg

  # 试运行模式,只显示将要上传的内容
  python upload_one.py --dry-run --all

  # 批量上传时设置间隔（默认30秒,防限流）
  python upload_one.py --all --interval 60
"""

import argparse
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# 确保工作目录正确
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).parent))

import yaml
from uploader import upload_to_bilibili, verify_credential

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
#  扫描 runs/ 目录
# ═══════════════════════════════════════════════════════

def scan_runs(runs_dir: str = "runs") -> list[dict]:
    """
    扫描 runs/ 下所有有效的 run 目录,返回状态列表.

    返回: [{
        "run_dir": Path,
        "run_info": dict,       # run_info.json 内容
        "publish_info": dict,   # publish_info 段
        "uploaded": bool,       # 是否已上传
        "bvid": str,            # B站 BV 号(已上传的)
        "video_exists": bool,   # 视频文件是否存在
        "cover_exists": bool,   # 封面是否存在
        "created_at": str,      # 创建时间(从目录名解析)
    }, ...]
    """
    runs_path = Path(runs_dir)
    if not runs_path.exists():
        return []

    results = []
    for d in sorted(runs_path.iterdir()):
        if not d.is_dir():
            continue
        info_file = d / "run_info.json"
        if not info_file.exists():
            continue

        try:
            run_info = json.loads(info_file.read_text())
        except (json.JSONDecodeError, IOError):
            continue

        pub = run_info.get("publish_info", {})
        if not pub:
            continue

        video_path = pub.get("video_path", "")
        cover_path = pub.get("cover_path", "")

        results.append({
            "run_dir": d,
            "run_info": run_info,
            "publish_info": pub,
            "uploaded": run_info.get("uploaded", False),
            "bvid": run_info.get("bvid", ""),
            "video_exists": bool(video_path) and os.path.exists(video_path),
            "cover_exists": bool(cover_path) and os.path.exists(cover_path),
            "created_at": run_info.get("created_at", d.name),
        })

    return results


def print_runs_status(runs: list[dict]):
    """打印所有 run 的上传状态"""
    if not runs:
        print("📭 runs/ 目录为空,没有可上传的视频")
        return

    print(f"\n{'─' * 70}")
    print(f"{'状态':^6} {'目录':^35} {'标题':^25}")
    print(f"{'─' * 70}")

    uploaded_count = 0
    pending_count = 0

    for r in runs:
        title = r["publish_info"].get("title", "无标题")[:24]
        dirname = r["run_dir"].name

        if r["uploaded"]:
            status = f"✅ {r['bvid']}"
            uploaded_count += 1
        elif not r["video_exists"]:
            status = "⚠️ 无视频"
        else:
            status = "⏳ 待上传"
            pending_count += 1

        print(f" {status:^8}  {dirname:<35} {title}")

    print(f"{'─' * 70}")
    print(f"  已上传: {uploaded_count}  |  待上传: {pending_count}  |  总计: {len(runs)}")
    print()


def print_dry_run_info(run: dict, index: int = None, total: int = None):
    """试运行模式: 打印将要上传的详细信息"""
    pub = run["publish_info"]
    prefix = f"  [{index}/{total}]" if index else "  "

    print(f"{prefix} 🏷️  标题: {pub.get('title', '无标题')}")
    print(f"  {'':>4} 📁 视频: {pub.get('video_path', '?')}")
    if run.get("cover_exists") or pub.get("cover_path"):
        cover = pub.get("cover_path", "无")
        exists = "✅" if run.get("cover_exists") else "❌ 文件不存在"
        print(f"  {'':>4} 🖼️  封面: {cover} {exists}")
    print(f"  {'':>4} 📝 描述: {pub.get('description', '无')[:60]}")
    tags = pub.get("tags", [])
    if tags:
        tag_str = ", ".join(tags) if isinstance(tags, list) else str(tags)
        print(f"  {'':>4} 🏷️  标签: {tag_str}")
    tid = pub.get("tid", "默认")
    print(f"  {'':>4} 📂 分区: {tid}")
    source = pub.get("source", "")
    if source:
        print(f"  {'':>4} 🔗 来源: {source}")
    print()


def upload_run(config: dict, run: dict, title_override: str = None,
               tid_override: int = None, cover_override: str = None,
               desc_override: str = None, tags_override: list = None,
               dry_run: bool = False) -> dict | None:
    """上传单个 run, 返回上传结果"""
    pub = run["publish_info"]
    run_dir = run.get("run_dir")

    if run["uploaded"]:
        logger.info(f"⏭️ 已上传,跳过: {run_dir.name if run_dir else '?'} (BV: {run['bvid']})")
        return {"status": "skipped", "bvid": run["bvid"], "reason": "already_uploaded"}

    if not run["video_exists"]:
        logger.warning(f"⚠️ 视频文件不存在,跳过: {pub.get('video_path', '?')}")
        return {"status": "skipped", "reason": "no_video"}

    title = title_override or pub.get("title", "新视频")
    tid = tid_override or pub.get("tid")
    desc = desc_override or pub.get("description", "")
    tags = tags_override or pub.get("tags")
    cover_path = cover_override or (pub.get("cover_path") if run.get("cover_exists") else None)

    # ── 试运行模式 ──
    if dry_run:
        dry_info = {**pub, "title": title, "description": desc, "tags": tags, "tid": tid}
        if cover_path:
            dry_info["cover_path"] = cover_path
        run_copy = {**run, "publish_info": dry_info, "cover_exists": bool(cover_path)}
        print_dry_run_info(run_copy)
        return {"status": "dry_run", "title": title}

    logger.info(f"\n🎬 上传: {title}")
    if run_dir:
        logger.info(f"   目录: {run_dir}")
    logger.info(f"   视频: {pub['video_path']}")
    if cover_path:
        logger.info(f"   封面: {cover_path}")
    if desc:
        logger.info(f"   描述: {desc[:50]}{'...' if len(desc) > 50 else ''}")
    if tags:
        tag_str = ", ".join(tags) if isinstance(tags, list) else str(tags)
        logger.info(f"   标签: {tag_str}")

    try:
        result = upload_to_bilibili(
            config=config,
            video_path=pub["video_path"],
            title=title,
            desc=desc,
            tags=tags,
            cover_path=cover_path,
            source_url=pub.get("source", ""),
            tid=tid,
        )

        # 更新 run_info（仅 run 目录模式）
        if result and result.get("bvid") and run_dir:
            run_info = run["run_info"]
            run_info["bvid"] = result["bvid"]
            run_info["uploaded"] = True
            run_info["uploaded_at"] = datetime.now().isoformat()

            info_file = run_dir / "run_info.json"
            info_file.write_text(json.dumps(run_info, ensure_ascii=False, indent=2))

        if result and result.get("bvid"):
            logger.info(f"✅ 上传成功! BV: {result['bvid']}")
            logger.info(f"   URL: {result.get('url', '')}")
            result["status"] = "success"
        else:
            logger.warning(f"⚠️ 上传返回异常结果: {result}")
            result = result or {}
            result["status"] = "unknown"

        return result

    except Exception as e:
        logger.error(f"❌ 上传失败: {e}")
        logger.error(f"   错误类型: {type(e).__name__}")
        logger.debug(f"   详细堆栈:\n{traceback.format_exc()}")
        return {"status": "failed", "error": str(e), "error_type": type(e).__name__}


def upload_video_direct(config: dict, video_path: str, title: str,
                        desc: str = "", tags: list = None,
                        cover_path: str = None, tid: int = None,
                        source_url: str = "", dry_run: bool = False) -> dict | None:
    """
    直接上传视频文件（无需 run 目录）.
    构造虚拟 run 结构复用 upload_run 逻辑.
    """
    video_p = Path(video_path)
    if not video_p.exists():
        logger.error(f"❌ 视频文件不存在: {video_path}")
        return {"status": "failed", "error": f"视频文件不存在: {video_path}"}

    if not title:
        # 用文件名作为默认标题
        title = video_p.stem

    # 构造虚拟 run 对象
    virtual_run = {
        "run_dir": None,
        "run_info": {},
        "publish_info": {
            "video_path": str(video_p.resolve()),
            "title": title,
            "description": desc,
            "tags": tags,
            "cover_path": str(Path(cover_path).resolve()) if cover_path else None,
            "source": source_url,
            "tid": tid,
        },
        "uploaded": False,
        "bvid": "",
        "video_exists": True,
        "cover_exists": bool(cover_path) and Path(cover_path).exists(),
        "created_at": datetime.now().isoformat(),
    }

    return upload_run(
        config=config, run=virtual_run,
        title_override=title, tid_override=tid,
        cover_override=cover_path if (cover_path and Path(cover_path).exists()) else None,
        desc_override=desc, tags_override=tags,
        dry_run=dry_run,
    )


def print_summary(results: list[dict], total: int):
    """打印批量上传结果汇总"""
    success = [r for r in results if r and r.get("status") == "success"]
    failed = [r for r in results if r and r.get("status") == "failed"]
    skipped = [r for r in results if r and r.get("status") == "skipped"]
    dry_runs = [r for r in results if r and r.get("status") == "dry_run"]
    unknown = [r for r in results if r and r.get("status") not in ("success", "failed", "skipped", "dry_run")]

    print(f"\n{'━' * 60}")
    print(f"📊 上传结果汇总")
    print(f"{'━' * 60}")

    if dry_runs:
        print(f"  🔍 试运行: {len(dry_runs)} 个")
    if success:
        print(f"  ✅ 成功: {len(success)} 个")
        for r in success:
            bvid = r.get("bvid", "?")
            url = r.get("url", "")
            print(f"     └─ {bvid}  {url}")
    if skipped:
        print(f"  ⏭️  跳过: {len(skipped)} 个")
        for r in skipped:
            reason = r.get("reason", "?")
            bvid = r.get("bvid", "")
            reason_text = {
                "already_uploaded": f"已上传 ({bvid})",
                "no_video": "视频文件不存在",
            }.get(reason, reason)
            print(f"     └─ {reason_text}")
    if failed:
        print(f"  ❌ 失败: {len(failed)} 个")
        for r in failed:
            err = r.get("error", "未知错误")
            err_type = r.get("error_type", "")
            print(f"     └─ [{err_type}] {err[:80]}")
    if unknown:
        print(f"  ⚠️  异常: {len(unknown)} 个")

    total_processed = len(success) + len(failed) + len(skipped) + len(dry_runs) + len(unknown)
    print(f"{'─' * 60}")
    print(f"  总计: {total_processed}/{total}")
    print()


# ═══════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="通用B站视频上传脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "run_dir", nargs="?", default=None,
        help="指定 run 目录路径 (如 runs/netflix_20260414_115632)"
    )
    parser.add_argument("--all", action="store_true", help="批量上传所有未上传的视频")
    parser.add_argument("--list", action="store_true", help="只列出状态,不上传")
    parser.add_argument("--title", help="覆盖视频标题")
    parser.add_argument("--tid", type=int, help="覆盖分区ID (183=影视剪辑, 17=影视杂谈, 211=美食)")
    parser.add_argument("--runs-dir", default="runs", help="runs 根目录 (默认: runs)")

    # 新增功能参数
    parser.add_argument("--dry-run", action="store_true", help="试运行模式,只显示将要上传的内容,不真正上传")
    parser.add_argument("--video", metavar="FILE", help="直接指定视频文件上传（无需 run 目录）")
    parser.add_argument("--cover", metavar="FILE", help="指定封面图片路径")
    parser.add_argument("--desc", help="视频描述/简介")
    parser.add_argument("--tags", help="视频标签,逗号分隔 (如: '标签1,标签2,标签3')")
    parser.add_argument("--source", help="转载来源 URL")
    parser.add_argument("--interval", type=int, default=30,
                        help="批量上传时每个视频之间的间隔秒数 (默认: 30, 防止限流)")
    args = parser.parse_args()

    # 解析 tags
    tags_list = None
    if args.tags:
        tags_list = [t.strip() for t in args.tags.split(",") if t.strip()]

    # 封面文件检查
    if args.cover and not Path(args.cover).exists():
        print(f"❌ 封面文件不存在: {args.cover}")
        sys.exit(1)

    # 加载配置
    config_path = Path("config.yaml")
    if not config_path.exists():
        print(f"❌ 配置文件不存在: {config_path.resolve()}")
        print("   请创建 config.yaml 或检查工作目录")
        sys.exit(1)
    try:
        config = yaml.safe_load(config_path.read_text())
    except Exception as e:
        print(f"❌ 配置文件解析失败: {e}")
        sys.exit(1)

    # ── 直接上传视频文件模式 ──
    if args.video:
        video_path = args.video
        if not Path(video_path).exists():
            print(f"❌ 视频文件不存在: {video_path}")
            sys.exit(1)

        if args.dry_run:
            print(f"\n🔍 [试运行] 直接上传视频文件:")
            print(f"{'─' * 50}")
            result = upload_video_direct(
                config=config, video_path=video_path,
                title=args.title or Path(video_path).stem,
                desc=args.desc or "", tags=tags_list,
                cover_path=args.cover, tid=args.tid,
                source_url=args.source or "", dry_run=True,
            )
            print(f"{'─' * 50}")
            print("🔍 试运行完成,以上内容未实际上传")
            return

        # 验证凭证
        print("🔑 验证B站凭证...")
        try:
            info = verify_credential(config)
            print(f"✅ 登录: {info['name']} (UID: {info['uid']}, Lv{info['level']})")
        except Exception as e:
            print(f"❌ 登录失败: {e}")
            print("   请先运行: python -c \"from uploader import qrcode_login; qrcode_login()\"")
            sys.exit(1)

        print(f"\n🎬 直接上传视频: {video_path}")
        result = upload_video_direct(
            config=config, video_path=video_path,
            title=args.title or Path(video_path).stem,
            desc=args.desc or "", tags=tags_list,
            cover_path=args.cover, tid=args.tid,
            source_url=args.source or "",
        )
        print_summary([result] if result else [], 1)
        return

    # 扫描 runs
    all_runs = scan_runs(args.runs_dir)

    # ── 列表模式 ──
    if args.list:
        print_runs_status(all_runs)
        return

    # ── 试运行 + 列表 ──
    if args.dry_run and not args.run_dir and not args.all:
        # 默认试运行：显示最新一个
        pending = [r for r in all_runs if not r["uploaded"] and r["video_exists"]]
        if not pending:
            print("📭 没有待上传的视频")
            print_runs_status(all_runs)
            return
        latest = pending[-1]
        print(f"\n🔍 [试运行] 将上传最新未上传视频:")
        print(f"{'─' * 50}")
        upload_run(config, latest, title_override=args.title, tid_override=args.tid,
                   cover_override=args.cover, desc_override=args.desc,
                   tags_override=tags_list, dry_run=True)
        print(f"{'─' * 50}")
        print("🔍 试运行完成,以上内容未实际上传")
        return

    # ── 验证凭证（非试运行都需要） ──
    if not args.dry_run:
        print("🔑 验证B站凭证...")
        try:
            info = verify_credential(config)
            print(f"✅ 登录: {info['name']} (UID: {info['uid']}, Lv{info['level']})")
        except Exception as e:
            print(f"❌ 登录失败: {e}")
            print("   请先运行: python -c \"from uploader import qrcode_login; qrcode_login()\"")
            sys.exit(1)

    # ── 指定目录上传 ──
    if args.run_dir:
        run_dir = Path(args.run_dir)
        matched = [r for r in all_runs if r["run_dir"] == run_dir or r["run_dir"].name == run_dir.name]
        if not matched:
            # 尝试直接加载
            info_file = run_dir / "run_info.json"
            if not info_file.exists():
                print(f"❌ 找不到 run_info.json: {run_dir}")
                sys.exit(1)
            try:
                run_info = json.loads(info_file.read_text())
            except (json.JSONDecodeError, IOError) as e:
                print(f"❌ 解析 run_info.json 失败: {e}")
                sys.exit(1)
            pub = run_info.get("publish_info", {})
            matched = [{
                "run_dir": run_dir,
                "run_info": run_info,
                "publish_info": pub,
                "uploaded": run_info.get("uploaded", False),
                "bvid": run_info.get("bvid", ""),
                "video_exists": bool(pub.get("video_path")) and os.path.exists(pub.get("video_path", "")),
                "cover_exists": bool(pub.get("cover_path")) and os.path.exists(pub.get("cover_path", "")),
                "created_at": run_info.get("created_at", ""),
            }]

        if args.dry_run:
            print(f"\n🔍 [试运行] 指定目录上传:")
            print(f"{'─' * 50}")

        result = upload_run(config, matched[0], title_override=args.title, tid_override=args.tid,
                            cover_override=args.cover, desc_override=args.desc,
                            tags_override=tags_list, dry_run=args.dry_run)

        if args.dry_run:
            print(f"{'─' * 50}")
            print("🔍 试运行完成,以上内容未实际上传")
        else:
            print_summary([result] if result else [], 1)
        return

    # ── 批量上传 ──
    if args.all:
        pending = [r for r in all_runs if not r["uploaded"] and r["video_exists"]]
        if not pending:
            print("📭 没有待上传的视频")
            print_runs_status(all_runs)
            return

        if args.dry_run:
            print(f"\n🔍 [试运行] 批量上传 {len(pending)} 个视频 (间隔 {args.interval}s):")
            print(f"{'═' * 50}")
            for i, run in enumerate(pending, 1):
                upload_run(config, run, title_override=args.title, tid_override=args.tid,
                           cover_override=args.cover, desc_override=args.desc,
                           tags_override=tags_list, dry_run=True)
            print(f"{'═' * 50}")
            print(f"🔍 试运行完成,以上 {len(pending)} 个视频未实际上传")
            return

        print(f"\n🚀 批量上传 {len(pending)} 个视频 (间隔 {args.interval}s)...")
        results = []
        for i, run in enumerate(pending, 1):
            print(f"\n{'═' * 50}")
            print(f"  [{i}/{len(pending)}] {run['publish_info'].get('title', '?')}")
            print(f"{'═' * 50}")
            result = upload_run(config, run, cover_override=args.cover,
                                desc_override=args.desc, tags_override=tags_list)
            results.append(result)

            # 间隔控制（最后一个不等待）
            if i < len(pending) and args.interval > 0:
                logger.info(f"⏳ 等待 {args.interval} 秒后继续 (防限流)...")
                time.sleep(args.interval)

        print_summary(results, len(pending))
        return

    # ── 默认: 上传最新一个未上传的 ──
    pending = [r for r in all_runs if not r["uploaded"] and r["video_exists"]]
    if not pending:
        print("📭 没有待上传的视频")
        print_runs_status(all_runs)
        return

    # 选最新的（列表已排序,取最后一个）
    latest = pending[-1]
    print(f"\n📌 自动选择最新未上传: {latest['run_dir'].name}")
    result = upload_run(config, latest, title_override=args.title, tid_override=args.tid,
                        cover_override=args.cover, desc_override=args.desc,
                        tags_override=tags_list)
    print_summary([result] if result else [], 1)


if __name__ == "__main__":
    main()
