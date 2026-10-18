"""Local-only health snapshot bridge. Never implies continuous Watch sampling."""
from datetime import datetime, timezone
import json
import math
from itertools import islice
from pathlib import Path
import time


class WellnessService:
    MAX_BYTES = 1_000_000
    MAX_FOLDER_FILES = 32
    MAX_FOLDER_BYTES = 4_000_000
    MAX_FOLDER_SAMPLES = 5000
    # Bounds reject corrupt input, not clinical categories or recommendations.
    FIELDS = {'heartRate':(1,400,'bpm'), 'restingHeartRate':(1,400,'bpm'),
              'hrv':(0,1000,'ms'), 'sleepHours':(0,24,'h')}

    def __init__(self, storage):
        self.storage = storage

    @classmethod
    def normalize(cls, data):
        if not isinstance(data, dict):
            raise ValueError('健康文件必须是 JSON 对象')
        if data.get('schemaVersion') == 2:
            return cls.normalize_samples(data)
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
                result['metrics'][key] = {'value':value,'at':at.astimezone(timezone.utc).isoformat(),'unit':unit,
                                          'source':str(item.get('source') or 'Apple Health')[:200]}
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ValueError(f'{key} 需要有效的 value 和带时区的采样时间 at') from None
        if not result['metrics']:
            raise ValueError('文件中没有可识别的心率、HRV、静息心率或睡眠数据')
        return result

    @staticmethod
    def sample_date(value):
        at = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if at.tzinfo is None or at.timestamp() > time.time()+300:
            raise ValueError('无效采样时间')
        return at.astimezone(timezone.utc)

    @classmethod
    def normalize_samples(cls, data):
        rows = data.get('samples')
        if not isinstance(rows, list) or len(rows) > 5000:
            raise ValueError('健康样本列表无效或过大')
        result = {'source':'Apple Health', 'metrics':{}, 'sources':[], 'warnings':[]}
        sleeps, sources, invalid = {}, set(), 0
        asleep = {'1','3','4','5','asleep','asleep (unspecified)','asleep unspecified',
                  'asleep (core)','asleep (deep)','asleep (rem)','core','deep','rem',
                  '睡眠','睡眠时间','睡着','已入睡','睡眠（未指定）','睡眠（核心）','睡眠（深度）',
                  '睡眠（快速眼动）','核心睡眠','深度睡眠','快速眼动睡眠','快速动眼睡眠'}
        ignored_sleep = {'0','2','in bed','inbed','awake','卧床','卧床时间','清醒'}
        for row in rows:
            try:
                kind = row['kind']
                at = cls.sample_date(row['at'])
                source = str(row.get('source') or '').strip()[:200]
                if kind == 'sleep':
                    category = str(row['value']).strip().lower()
                    if category in ignored_sleep:
                        continue
                    if category not in asleep or not source:
                        raise ValueError('未知睡眠阶段或来源')
                    end = cls.sample_date(row['end'])
                    if not 0 < (end-at).total_seconds() <= 86400:
                        raise ValueError('无效睡眠时段')
                    sleeps.setdefault(source, []).append((at.timestamp(),end.timestamp()))
                    sources.add(source)
                    continue
                if kind not in ('heartRate','restingHeartRate','hrv'):
                    raise ValueError('未知指标')
                raw = row['value']
                if isinstance(raw,bool):
                    raise ValueError('无效数值')
                value = float(raw)
                unit = str(row.get('unit','')).strip().lower()
                allowed = {'ms','毫秒'} if kind == 'hrv' else {'count/min','bpm','beats/min','次/分','次/分钟'}
                if kind == 'hrv' and unit in ('s','秒'):
                    value *= 1000
                elif unit not in allowed:
                    raise ValueError('未知单位')
                low, high, normalized_unit = cls.FIELDS[kind]
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError('无效数值')
                metric = {'value':value,'at':at.isoformat(),'unit':normalized_unit,'source':source or 'Apple Health'}
                previous = result['metrics'].get(kind)
                if not previous or metric['at'] > previous['at']:
                    result['metrics'][kind] = metric
                if source:
                    sources.add(source)
            except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
                invalid += 1
        # Merge intervals separately for each source, then use its most recent bout.
        # Never add Watch and Fitbit recordings together.
        bouts = []
        for source, intervals in sleeps.items():
            merged = []
            for start,end in sorted(intervals):
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(end,merged[-1][1])
                else:
                    merged.append([start,end])
            groups = []
            for start,end in merged:
                if not groups or start-groups[-1][-1][1] > 90*60:
                    groups.append([])
                groups[-1].append((start,end))
            if groups:
                latest = groups[-1]
                bouts.append((latest[-1][1],source,sum(b-a for a,b in latest)/3600))
        if bouts:
            end,source,hours = max(bouts)
            if 0 < hours <= 24:
                result['metrics']['sleepHours'] = {'value':hours,'at':datetime.fromtimestamp(end,timezone.utc).isoformat(),
                                                   'unit':'h','source':source}
        result['sources'] = sorted(sources)
        if invalid:
            result['warnings'].append(f'{invalid} 条样本的格式或单位无法识别，已跳过')
        if not result['metrics']:
            raise ValueError('没有可用健康样本，请在 iPhone 运行快捷指令并检查读取授权、单位和采样时间')
        return result

    def import_data(self, data):
        snapshot = self.normalize(data)
        self.storage.update(lambda d:d.update(snapshot=snapshot,sourcePath=None))
        return self.read()

    def configure(self, value):
        path = Path(str(value.get('path',''))).expanduser()
        if not path.is_absolute() or not (path.is_dir() or (path.suffix.lower() == '.json' and path.is_file())):
            raise ValueError('请选择本机或 iCloud Drive 中的 JSON 文件或健康同步文件夹')
        self.load_source(path) # Validate before replacing the working source.
        self.storage.update(lambda d:d.update(sourcePath=str(path),snapshot=None))
        return self.read()

    def load_source(self, path):
        return self.load_folder(path) if path.is_dir() else self.load_file(path)

    @staticmethod
    def decode_json(raw):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError('健康文件暂不可读，请等待同步完成') from None

    def load_file(self, path):
        with path.open('rb') as f:
            raw = f.read(self.MAX_BYTES+1)
        if len(raw) > self.MAX_BYTES:
            raise ValueError('请使用最近健康样本的 JSON（小于 1 MB）')
        return self.normalize(self.decode_json(raw))

    def load_folder(self, path):
        # Only direct children of the explicitly selected folder are eligible.
        files = sorted(islice(path.glob('wokyis-health*.json'), self.MAX_FOLDER_FILES+1))
        if len(files) > self.MAX_FOLDER_FILES:
            raise ValueError(f'健康同步文件过多，请保留不超过 {self.MAX_FOLDER_FILES} 个最近文件')
        snapshots, samples, invalid = [], [], 0
        total_size, total_read = 0, 0
        for file in files:
            if file.is_symlink() or not file.is_file():
                invalid += 1
                continue
            try:
                size = file.stat().st_size
            except OSError:
                invalid += 1
                continue
            total_size += size
            if total_size > self.MAX_FOLDER_BYTES:
                raise ValueError('健康同步文件总大小超过 4 MB，请只保留最近的样本文件')
            try:
                with file.open('rb') as f:
                    raw = f.read(min(self.MAX_BYTES, self.MAX_FOLDER_BYTES-total_read)+1)
            except OSError:
                invalid += 1
                continue
            total_read += len(raw)
            if total_read > self.MAX_FOLDER_BYTES:
                raise ValueError('健康同步文件总大小超过 4 MB，请只保留最近的样本文件')
            try:
                if len(raw) > self.MAX_BYTES:
                    raise ValueError('健康文件超过 1 MB')
                data = self.decode_json(raw)
                snapshot = self.normalize(data)
            except ValueError:
                invalid += 1
                continue
            if data.get('schemaVersion') == 2:
                samples.extend(data['samples'])
                if len(samples) > self.MAX_FOLDER_SAMPLES:
                    raise ValueError('健康同步样本过多，请只保留最近的样本文件')
            else:
                snapshots.append(snapshot)
        # Normalize raw v2 rows together so copied files and overlapping sleep
        # segments are merged by their actual source and interval, never added.
        if samples:
            snapshots.append(self.normalize_samples({'samples':samples}))
        result = {'source':'Apple Health', 'metrics':{}, 'sources':[], 'warnings':[]}
        sources = set()
        for snapshot in snapshots:
            sources.update(snapshot.get('sources',[]))
            result['warnings'].extend(snapshot.get('warnings',[]))
            for key, metric in snapshot['metrics'].items():
                sources.add(metric['source'])
                previous = result['metrics'].get(key)
                if not previous or self.sample_date(metric['at']) > self.sample_date(previous['at']):
                    result['metrics'][key] = metric
        if not result['metrics']:
            raise ValueError('文件夹内没有可用健康数据，请运行 iPhone 快捷指令并等待 wokyis-health 开头的 JSON 文件同步')
        result['sources'] = sorted(sources)
        if invalid:
            result['warnings'].insert(0,f'{invalid} 个健康同步文件暂不可读或没有有效数据，已跳过')
        return result

    def read(self):
        data = self.storage.read()
        result = {'connected':False,'metrics':{},'mode':None,'error':None}
        try:
            if data.get('sourcePath'):
                path = Path(data['sourcePath'])
                result.update(self.load_source(path),connected=True,mode='folder' if path.is_dir() else 'file')
            elif data.get('snapshot'):
                result.update(data['snapshot'],connected=True,mode='import')
        except (ValueError, OSError) as e:
            result['error'] = str(e) if isinstance(e,ValueError) else '同步文件暂不可用，请确认 iCloud 已下载该文件'
        return result

    def disconnect(self):
        self.storage.update(lambda d:d.clear())
