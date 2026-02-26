import os
from typing import Any, Dict, Optional


class ConfigLoader:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._load_config()

    def _load_config(self):
        # API设置
        api_settings = self.config.get('api_settings', {})
        self.provider_id = api_settings.get('provider_id', '')
        self.api_type = api_settings.get('api_type', 'doubao')
        self.model = api_settings.get('model', '')
        
        # 从 provider_overrides 获取豆包配置
        self.api_keys = []
        self.endpoint_id = 'doubao-seedream-4-5-251128'
        self.api_base = 'https://ark.cn-beijing.volces.com'
        self.default_size = '2K'
        self.watermark = False
        self.optimize_prompt_mode = 'standard'
        
        provider_overrides = api_settings.get('provider_overrides', [])
        doubao_settings = {}
        
        # 从 provider_overrides 中查找 doubao 配置
        if isinstance(provider_overrides, list):
            for override in provider_overrides:
                if isinstance(override, dict) and override.get('__template_key') == 'doubao':
                    doubao_settings = override.copy()
                    doubao_settings.pop('__template_key', None)
                    break
        
        # 处理 API 密钥
        if 'api_keys' in doubao_settings:
            api_keys = doubao_settings.get('api_keys') or []
            if isinstance(api_keys, list):
                # 清理并过滤空字符串
                self.api_keys = [k.strip() for k in api_keys if isinstance(k, str) and k.strip()]
        # 兼容旧的 api_key（单个 key）
        elif 'api_key' in doubao_settings and isinstance(doubao_settings['api_key'], str):
            key = doubao_settings['api_key'].strip()
            self.api_keys = [key] if key else []
        
        # 处理其他配置
        if doubao_settings:
            self.endpoint_id = doubao_settings.get('endpoint_id', self.endpoint_id)
            self.api_base = doubao_settings.get('api_base', self.api_base)
            self.default_size = doubao_settings.get('default_size', self.default_size)
            self.watermark = doubao_settings.get('watermark', self.watermark)
            self.optimize_prompt_mode = doubao_settings.get('optimize_prompt_mode', self.optimize_prompt_mode)

        # 图像生成设置
        image_generation_settings = self.config.get('image_generation_settings', {})
        self.resolution = image_generation_settings.get('resolution', '1K')
        self.aspect_ratio = image_generation_settings.get('aspect_ratio', '1:1')

        # 人设设置
        persona_settings = self.config.get('persona_settings', {})
        self.persona_reference_image = persona_settings.get('persona_reference_image', [])
        self.enable_auto_outfit = persona_settings.get('enable_auto_outfit', True)

        # 重试设置
        retry_settings = self.config.get('retry_settings', {})
        self.max_attempts_per_key = retry_settings.get('max_attempts_per_key', 3)
        self.enable_smart_retry = retry_settings.get('enable_smart_retry', True)
        self.total_timeout = retry_settings.get('total_timeout', 120)

        # 限流设置
        rate_limit_settings = self.config.get('rate_limit_settings', {})
        self.rate_limit_enabled = rate_limit_settings.get('enabled', True)
        self.rate_limit_max_requests = rate_limit_settings.get('max_requests', 5)
        self.rate_limit_period = rate_limit_settings.get('period_seconds', 60)

    def get_api_settings(self) -> Dict[str, Any]:
        return {
            'provider_id': self.provider_id,
            'api_type': self.api_type,
            'model': self.model,
            'api_keys': self.api_keys,
            'endpoint_id': self.endpoint_id,
            'api_base': self.api_base,
            'default_size': self.default_size,
            'watermark': self.watermark,
            'optimize_prompt_mode': self.optimize_prompt_mode
        }

    def get_image_generation_settings(self) -> Dict[str, Any]:
        return {
            'resolution': self.resolution,
            'aspect_ratio': self.aspect_ratio
        }

    def get_persona_settings(self) -> Dict[str, Any]:
        return {
            'persona_reference_image': self.persona_reference_image,
            'enable_auto_outfit': self.enable_auto_outfit
        }

    def get_retry_settings(self) -> Dict[str, Any]:
        return {
            'max_attempts_per_key': self.max_attempts_per_key,
            'enable_smart_retry': self.enable_smart_retry,
            'total_timeout': self.total_timeout
        }

    def get_rate_limit_settings(self) -> Dict[str, Any]:
        return {
            'enabled': self.rate_limit_enabled,
            'max_requests': self.rate_limit_max_requests,
            'period_seconds': self.rate_limit_period
        }
