# Virtual2Road

把 Garmin Edge 记录的室内／虚拟骑行 FIT 活动分类改为公路骑行
（`sport=cycling`、`sub_sport=road`），保留原始传感器数据。

脚本只修改活动元数据，不会生成真实 GPS 轨迹，也不会把室内速度、距离或海拔
换算为真实路面的数值。Garmin Connect 等平台最终如何分类，需要实际导入验证。

## 环境

要求 Python 3.9+。在项目目录运行以下命令，直接使用本项目的 `.venv`，无需激活：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 使用

先检查原文件的 CRC 和运动分类（不写入文件）：

```powershell
.\.venv\Scripts\python.exe fit2road.py "ride.fit" --inspect
```

转换单个文件，生成同目录下的 `ride_road.fit`：

```powershell
.\.venv\Scripts\python.exe fit2road.py "ride.fit"
```

批量处理并将结果集中写入指定目录：

```powershell
.\.venv\Scripts\python.exe fit2road.py "*.fit" --output-dir road_files
```

- 原文件不被覆盖，已有的输出文件也不被覆盖。
- 不指定输出目录时，已经符合目标元数据的文件会被跳过。
- 指定 `--output-dir` 时，每个成功处理的输入都会生成一个 `_road.fit` 文件；
  无需修改的文件会原样复制。
- 只支持所有 session 均为骑行的 Activity FIT 文件。session 必须已有
  `sub_sport` 字段；缺失时会停止处理该文件，不会重建 FIT 消息。
- lap、sport、segment_lap、split、split_summary 中缺失或为无效值 `0xff`
  的 `sport` 沿用已验证的骑行类型；明确标为其他运动的消息保留原样。
- 只修改已有的分类字段，并在空间足够时把活动配置名称设为 `ROAD`。
  短名称字段保持原样，不扩展文件。
- 心率、功率、踏频、坐标、时间戳、内嵌训练、开发者字段和未知消息保持原始字节。
- 支持标准 12/14 字节文件头。CRC 错误、截断、多份 FIT 拼接等情况会报错。
- 批量处理遇到错误会继续处理其他输入；只要有失败，最终退出码就是 1。

## 验证

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

