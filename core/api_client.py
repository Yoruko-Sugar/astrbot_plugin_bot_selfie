import asyncio
import base64
import os
import random
import re
import time
from typing import Dict, List, Optional, Tuple

import aiohttp

from astrbot.api import logger


class ApiClient:
    def __init__(self, api_keys: List[str], api_base: str = '', endpoint_id: str = 'doubao-seedream-4-5-251128'):
        self.api_keys = api_keys
        self.api_base = api_base or 'https://ark.cn-beijing.volces.com'
        self.endpoint_id = endpoint_id
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def generate_image(self, prompt: str, reference_image: Optional[str] = None, resolution: str = '1K') -> Tuple[bool, str]:
        """生成图像"""
        if not self.api_keys:
            return False, "API密钥未配置"

        # 随机选择一个API密钥
        api_key = random.choice(self.api_keys)

        # 构建API URL
        api_base = self.api_base.rstrip('/')
        url = f"{api_base}/api/v3/images/generations"

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        }

        # 准备请求参数
        payload = {
            'model': self.endpoint_id,
            'prompt': prompt,
            'response_format': 'url',
            'watermark': False
        }

        # 处理分辨率
        size = self._map_resolution(resolution, self.endpoint_id)
        if size:
            payload['size'] = size

        # 处理参考图
        if reference_image and os.path.exists(reference_image):
            with open(reference_image, 'rb') as f:
                image_data = base64.b64encode(f.read()).decode('utf-8')
            # 豆包 API 图生图需要使用 image 字段，格式为 data:image/xxx;base64,xxx
            # 根据文件扩展名确定 MIME 类型
            ext = os.path.splitext(reference_image)[1].lower()
            mime_type = 'image/png'
            if ext in ['.jpg', '.jpeg']:
                mime_type = 'image/jpeg'
            elif ext == '.webp':
                mime_type = 'image/webp'
            elif ext == '.gif':
                mime_type = 'image/gif'
            
            payload['image'] = f'data:{mime_type};base64,{image_data}'
            logger.info(f"添加参考图片到请求: {reference_image}")

        # 打印请求信息（隐藏敏感信息）
        safe_headers = headers.copy()
        if 'Authorization' in safe_headers:
            safe_headers['Authorization'] = f"Bearer {api_key[:8]}...{api_key[-4:]}"
        
        logger.info(f"API请求URL: {url}")
        logger.info(f"API请求Headers: {safe_headers}")
        
        # 打印payload（如果有image字段，只显示前100个字符）
        safe_payload = payload.copy()
        if 'image' in safe_payload and len(safe_payload['image']) > 100:
            safe_payload['image'] = safe_payload['image'][:100] + '...(truncated)'
        logger.info(f"API请求Payload: {safe_payload}")
        
        session = await self._get_session()
        
        try:
            async with session.post(url, headers=headers, json=payload) as response:
                logger.info(f"API响应状态码: {response.status}")
                
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"API响应错误: {error_text}")
                    return False, f"API请求失败: {response.status} - {error_text}"
                
                data = await response.json()
                logger.info(f"API响应数据: {data}")
                
                # 处理响应
                if 'data' not in data or not data['data']:
                    return False, "API返回格式错误"
                
                # 提取图像URL
                image_urls = []
                for item in data['data']:
                    if 'url' in item:
                        image_urls.append(item['url'])
                    elif 'b64_json' in item:
                        # 处理base64编码的图像
                        image_path = self._save_base64_image(item['b64_json'])
                        if image_path:
                            image_urls.append(image_path)
                
                if not image_urls:
                    return False, "未生成图像"
                
                # 返回第一个图像URL
                return True, image_urls[0]
                
        except Exception as e:
            return False, f"API请求异常: {str(e)}"

    def _map_resolution(self, resolution: str, model: str) -> str:
        """映射分辨率到豆包API支持的格式"""
        if not resolution:
            return '2K'

        raw = str(resolution).strip()
        if not raw:
            return '2K'

        normalized = raw.lower().replace(' ', '')
        model_lower = model.lower() if model else ''

        # WxH格式 - 直接返回
        if re.match(r"^\d{3,5}x\d{3,5}$", normalized):
            return normalized

        # 处理1K/2K/4K格式
        if normalized in {'1k', '1024'}:
            return '1K'
        if normalized in {'2k', '2048'}:
            return '2K'
        if normalized in {'4k', '4096'}:
            return '4K'

        # 默认返回2K
        return '2K'

    def _save_base64_image(self, b64_data: str) -> str:
        """保存base64编码的图像到本地"""
        try:
            # 移除data URI前缀
            if b64_data.startswith('data:'):
                b64_data = b64_data.split(';base64,')[-1]
            
            # 解码base64数据
            image_data = base64.b64decode(b64_data)
            
            # 生成唯一文件名
            timestamp = int(time.time() * 1000)
            image_path = f"selfie_{timestamp}.png"
            
            # 保存图像
            with open(image_path, 'wb') as f:
                f.write(image_data)
            
            return image_path
        except Exception as e:
            logger.error(f"保存图像失败: {e}")
            return ''
