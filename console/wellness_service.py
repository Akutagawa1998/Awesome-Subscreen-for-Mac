"""Local-only health snapshot bridge. Never implies continuous Watch sampling."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time


class WellnessService:
    # Bounds reject corrupt input, not clinical categories or recommendations.
    FIELDS = {'heartRate':(1,400,'bpm'), 'restingHeartRate':(1,400,'bpm'),
              'hrv':(0,1000,'ms'), 'sleepHours':(0,24,'h')}

    def __init__(self, storage):
        self.storage = storage

    @classmethod
    def normalize(cls, data):
        if not isinstance(data, dict):
            raise ValueError('健康文件必须是 JSON 对象')
        result = {'source':'Apple Health', 'metrics':{}}
        for key, (low, high, unit) in cls.FIELDS.items():
            item = data.get(key)
            if item is None:
                continue
            try:
                value = item['value']
                if type(value) not in (float,int) or not math.isfinite(value) or not low <= value <= high:
                    raise ValueError()
                at = datetime.fromisoformat(item['at'].replace('Z','+00:00'))
                if at.tzinfo is None or at.timestamp() > time.time()+300:
                    raise ValueError()
                result['metrics'][key] = {'value':value,'at':at.astimezone(timezone.utc).isoformat(),'unit':unit}
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ValueError(f'{key} 需要有效的 value 和带时区的采样时间 at') from None
        if not result['metrics']:
            raise ValueError('文件中没有可识别的心率、HRV、静息心率或睡眠数据')
        return result

    def import_data(self, data):
        snapshot = self.normalize(data)
        self.storage.update(lambda d:d.update(snapshot=snapshot,sourcePath=None))
        return self.read()

    def configure(self, value):
        path = Path(str(value.get('path',''))).expanduser()
        if not path.is_absolute() or path.suffix.lower() != '.json' or not path.is_file():
            raise ValueError('请选择本机或 iCloud Drive 中的 JSON 文件')
        self.load_file(path) # Validate before replacing the working source.
        self.storage.update(lambda d:d.update(sourcePath=str(path),snapshot=None))
        return self.read()

    def load_file(self, path):
        with path.open('rb') as f:
            raw = f.read(100001)
        if len(raw) > 100000:
            raise ValueError('请使用最新指标的精简 JSON（小于 100 KB）')
        try:
            return self.normalize(json.loads(raw))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError('健康文件暂不可读，请等待同步完成') from None

    def read(self):
        data = self.storage.read()
        result = {'connected':False,'metrics':{},'mode':None,'error':None}
        try:
            if data.get('sourcePath'):
                result.update(self.load_file(Path(data['sourcePath'])),connected=True,mode='file')
            elif data.get('snapshot'):
                result.update(data['snapshot'],connected=True,mode='import')
        except (ValueError, OSError) as e:
            result['error'] = str(e) if isinstance(e,ValueError) else '同步文件暂不可用，请确认 iCloud 已下载该文件'
        return result

    def disconnect(self):
        self.storage.update(lambda d:d.clear())
