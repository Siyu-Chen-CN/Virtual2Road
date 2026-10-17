# Virtual2Road

把 Garmin Edge 记录的室内／虚拟骑行 FIT 活动分类改为公路骑行
（`sport=cycling`、`sub_sport=road`），保留原始传感器数据。

脚本只修改活动元数据，不会生成真实 GPS 轨迹，也不会把室内速度、距离或海拔
换算为真实路面的数值。Garmin Connect 等平台最终如何分类，需要实际导入验证。

## 目录结构

```text
Virtual2Road/
├── src/                        # 转换逻辑和图形界面
│   ├── fit2road.py              # 命令行入口 / 核心转换
│   └── fit2road_gui.py          # 图形界面入口
├── tests/                      # 转换与界面测试
├── scripts/
│   └── build.ps1               # Windows exe 构建脚本
├── packaging/
│   ├── fit2road.manifest        # Windows 高 DPI 配置
│   └── requirements-build.txt  # 打包依赖
├── dist/
│   └── fit2road.exe            # 可直接运行的程序（构建生成）
├── requirements.txt            # 运行依赖
└── README.md
```

`.venv/` 为本机 Python 环境，`build/` 为可重新生成的打包缓存。
这两个目录及 `dist/` 不纳入 Git；测试源码纳入版本管理。
以下命令均从项目根目录执行。

## 环境

要求 Python 3.9+。在项目目录运行以下命令，直接使用本项目的 `.venv`，无需激活：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 图形界面（Windows）

双击 `dist\fit2road.exe` 启动，无需安装 Python。生成 exe 的方法见下方。

界面支持 Windows 高 DPI 显示，启动时按系统缩放比例绘制字体和控件，
避免 Windows 将低分辨率界面整体放大造成模糊。默认窗口大小也随缩放比例调整。

1. 点击「添加 FIT 文件」，可同时选择多个文件。
2. 默认保存在各原文件旁；也可点击「选择目录」统一设置保存位置。
3. 点击「一键转换」，在列表中查看每个文件的结果。
4. 点击「打开输出目录」查看生成的文件；选中某个成功行可打开该文件的输出目录。

输出名为 `原文件名_road.fit`，重名时使用 `原文件名_road (2).fit` 等编号，
不会覆盖原文件或已有结果。已符合目标元数据的文件也会复制一份输出。
单个文件失败不影响其他文件，选中失败行可查看原因。所有处理在本机进行，无需上传文件。

也可以直接从源码启动界面：

```powershell
.\.venv\Scripts\python.exe src\fit2road_gui.py
```

### 打包 exe

在 Windows 项目目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

脚本会安装构建依赖并生成 `dist\fit2road.exe`；分发时只需复制这个 exe。
构建使用 [PyInstaller](https://pyinstaller.org/en/stable/usage.html) 的单文件、无控制台模式。

## 命令行使用

先检查原文件的 CRC 和运动分类（不写入文件）：

```powershell
.\.venv\Scripts\python.exe src\fit2road.py "ride.fit" --inspect
```

转换单个文件，生成同目录下的 `ride_road.fit`：

```powershell
.\.venv\Scripts\python.exe src\fit2road.py "ride.fit"
```

批量处理并将结果集中写入指定目录：

```powershell
.\.venv\Scripts\python.exe src\fit2road.py "*.fit" --output-dir road_files
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

