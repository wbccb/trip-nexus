import json
from datetime import datetime, date, timedelta

# 自定义JSON编码器，处理所有datetime/date对象，因为datetime不能json化
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        # 处理datetime和date对象，转为ISO字符串
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        # 若有其他不可序列化的类型（如timedelta），可补充处理
        elif isinstance(obj, timedelta):
            return str(obj)
        # 其他类型调用父类默认方法
        return super().default(obj)