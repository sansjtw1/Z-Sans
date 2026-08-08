import gettext
import yaml
import os
import logging
import gettext
from pathlib import Path

logger = logging.getLogger('zsans.i18n')

_translation = None
_current_language = 'en'


def setup_i18n(config_path):
    global _translation, _current_language
    # 默认配置
    default_config = {
        'language': {
            'default_language': 'zh_CN',
            'supported_languages': ['zh_CN', 'en'],
            'locale_dir': 'i18n'
        }
    }

    # 加载配置文件
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Failed to load config file: {e}")
        config = {}

    # 合并配置
    language_config = config.get('language', {})
    default_language = language_config.get('default_language', default_config['language']['default_language'])
    supported_languages = language_config.get('supported_languages', default_config['language']['supported_languages'])
    locale_dir = language_config.get('locale_dir', default_config['language']['locale_dir'])

    # 检查语言是否受支持
    if default_language not in supported_languages:
        logger.warning(f"Language {default_language} not in supported languages {supported_languages}, using zh_CN")
        default_language = 'zh_CN'

    # 确保locale_dir存在
    locale_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), locale_dir)
    if not os.path.exists(locale_dir):
        logger.error(f"Locale directory {locale_dir} does not exist")
        # 使用默认路径作为备选
        locale_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'i18n')
        if not os.path.exists(locale_dir):
            logger.error(f"Default locale directory {locale_dir} also does not exist")
            return

    # 初始化gettext
    _current_language = default_language
    try:
        _translation = gettext.translation(
            'messages',
            localedir=locale_dir,
            languages=[default_language],
            fallback=True
        )
        _translation.install()
        logger.info(f"Successfully set up i18n with language {default_language}")
    except Exception as e:
        logger.error(f"Failed to set up i18n: {e}")
        # Fallback to English if initialization fails
        _current_language = 'en'
        _translation = gettext.translation(
            'messages',
            localedir=str(Path(__file__).parent.parent / 'i18n'),
            languages=['en'],
            fallback=True
        )
        _translation.install()



def _(message):
    if _translation:
        return _translation.gettext(message)
    return message


def get_current_language():
    return _current_language