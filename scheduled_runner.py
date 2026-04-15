#!/usr/bin/env python3
"""
定时搬运脚本 - 根据时间选择主题并运行pipeline
每天10:00, 12:00, 14:00, 16:00运行，顺序：预告片-旅行-美食-预告片
"""

import sys
import os
import json
import logging
from datetime import datetime
from pathlib import Path

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(__file__))

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('scheduled_runs.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 定义主题顺序
THEME_SEQUENCE = ['netflix', 'travel', 'food', 'netflix']

# 定义运行时间
RUN_HOURS = [10, 12, 14, 16]

def get_current_theme_index():
    """根据当前时间确定应该运行哪个主题"""
    now = datetime.now()
    current_hour = now.hour
    
    # 找到当前小时在运行时间中的索引
    try:
        index = RUN_HOURS.index(current_hour)
        return index
    except ValueError:
        # 如果当前时间不在运行时间范围内，返回-1
        return -1

def get_theme_for_run(run_number=None):
    """根据运行次数确定主题"""
    if run_number is None:
        # 基于当前时间
        index = get_current_theme_index()
        if index == -1:
            logger.warning(f"当前时间 {datetime.now().strftime('%H:%M')} 不在计划运行时间内")
            return None
    else:
        # 基于运行次数（循环使用）
        index = run_number % len(THEME_SEQUENCE)
    
    theme = THEME_SEQUENCE[index]
    logger.info(f"选择主题: {theme} (索引: {index})")
    return theme

def load_counter():
    """加载运行计数器"""
    counter_file = Path(__file__).parent / 'run_counter.json'
    if counter_file.exists():
        try:
            with open(counter_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('count', 0)
        except (json.JSONDecodeError, IOError):
            pass
    return 0

def save_counter(count):
    """保存运行计数器"""
    counter_file = Path(__file__).parent / 'run_counter.json'
    with open(counter_file, 'w', encoding='utf-8') as f:
        json.dump({'count': count, 'last_updated': datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)

def send_notification(success, theme, video_info=None, error_msg=None):
    """发送微信通知"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if success:
        title = f"✅ 视频搬运成功 - {theme.upper()}"
        message = f"主题: {theme.upper()}\n"
        if video_info:
            message += f"标题: {video_info.get('title', '未知')}\n"
            message += f"URL: {video_info.get('url', '未知')}\n"
        message += f"时间: {timestamp}\n"
        message += f"状态: 成功发布到B站"
    else:
        title = f"❌ 视频搬运失败 - {theme.upper()}"
        message = f"主题: {theme.upper()}\n"
        message += f"错误: {error_msg}\n"
        message += f"时间: {timestamp}\n"
        message += f"状态: 失败"
    
    logger.info(f"通知: {title}\n{message}")
    
    # 尝试发送微信通知
    try:
        # 从配置文件中读取微信通知配置
        config_path = Path(__file__).parent / 'config.yaml'
        if config_path.exists():
            import yaml
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            
            # 获取微信通知配置
            wechat_config = config.get('wechat_notification', {})
            if wechat_config.get('enabled', False):
                send_wechat_notification(title, message, wechat_config)
            else:
                logger.info("微信通知未启用")
        else:
            logger.warning("配置文件不存在，跳过微信通知")
    except Exception as e:
        logger.error(f"发送微信通知时出错: {e}")
    
    # 同时打印到控制台
    print(f"\n{'='*50}")
    print(f"通知: {title}")
    print(f"{'='*50}")
    print(message)
    print(f"{'='*50}\n")
    
    return f"{title}\n{message}"

def send_wechat_notification(title, message, config):
    """通过Server酱发送微信通知"""
    try:
        import requests
        
        # Server酱配置
        send_key = config.get('send_key', '')
        if not send_key:
            logger.warning("未配置Server酱SendKey，跳过微信通知")
            return False
        
        # Server酱API
        url = f"https://sc.ftqq.com/{send_key}.send"
        
        # 发送请求
        data = {
            'text': title,
            'desp': message
        }
        
        response = requests.post(url, data=data, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            if result.get('errno') == 0:
                logger.info("微信通知发送成功")
                return True
            else:
                logger.error(f"微信通知发送失败: {result.get('errmsg', '未知错误')}")
                return False
        else:
            logger.error(f"微信通知请求失败，状态码: {response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"发送微信通知时出错: {e}")
        return False

def run_pipeline_with_theme(theme, use_counter=False):
    """运行指定主题的pipeline"""
    logger.info(f"开始运行主题: {theme}")
    
    # 设置代理环境变量
    os.environ['https_proxy'] = 'http://127.0.0.1:7897'
    os.environ['http_proxy'] = 'http://127.0.0.1:7897'
    os.environ['all_proxy'] = 'socks5://127.0.0.1:7897'
    logger.info("已设置代理环境变量")
    
    try:
        # 导入main模块
        from main import run_pipeline, load_config
        
        # 加载配置
        config = load_config('config.yaml')
        
        # 设置只发布到B站
        config['_no_upload'] = False  # 确保上传功能开启
        
        # 运行pipeline
        result = run_pipeline(config, theme, platforms=['bilibili'])
        
        if result:
            logger.info(f"主题 {theme} 运行成功")
            # 发送成功通知
            send_notification(True, theme, result)
            return True
        else:
            logger.error(f"主题 {theme} 运行失败")
            # 发送失败通知
            send_notification(False, theme, error_msg="Pipeline返回空结果")
            return False
            
    except Exception as e:
        logger.error(f"运行主题 {theme} 时发生错误: {e}")
        import traceback
        traceback.print_exc()
        # 发送错误通知
        send_notification(False, theme, error_msg=str(e))
        return False

def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("定时搬运任务开始")
    logger.info(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 解析命令行参数
    import argparse
    parser = argparse.ArgumentParser(description='定时搬运脚本')
    parser.add_argument('--theme', type=str, help='指定主题（覆盖自动选择）')
    parser.add_argument('--use-counter', action='store_true', help='使用计数器而不是时间选择主题')
    parser.add_argument('--dry-run', action='store_true', help='只显示将要运行的主题，不实际运行')
    args = parser.parse_args()
    
    # 确定主题
    if args.theme:
        theme = args.theme
        logger.info(f"使用指定主题: {theme}")
    else:
        if args.use_counter:
            # 使用计数器
            counter = load_counter()
            theme = get_theme_for_run(counter)
            if theme:
                save_counter(counter + 1)
                logger.info(f"使用计数器选择主题，当前计数: {counter}")
        else:
            # 使用时间
            theme = get_theme_for_run()
    
    if not theme:
        logger.error("无法确定要运行的主题")
        return 1
    
    if args.dry_run:
        logger.info(f"DRY RUN: 将运行主题 {theme}")
        print(f"DRY RUN: 将运行主题 {theme}")
        return 0
    
    # 运行pipeline
    success = run_pipeline_with_theme(theme)
    
    if success:
        logger.info("定时搬运任务完成")
        return 0
    else:
        logger.error("定时搬运任务失败")
        return 1

if __name__ == '__main__':
    sys.exit(main())