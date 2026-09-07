# 体态评估系统

基于 Python 3.12、PySide6 和 SQLite 的 Windows 桌面应用。现已实现患者档案、五姿势 Azure Kinect 体态采集、单机四方向骨盆体态筛查、硬质量门、体表点云代理指标、人工复核和本地结果持久化。

## 运行

项目已经在 `.venv` 中安装依赖：

```powershell
& "D:\体态评估系统\start.ps1"
```

脚本会自动进入项目目录并清除仅用于测试的离屏环境变量，因此可以从任意 PowerShell 目录执行。

默认登录账号：

- 账号：`admin`
- 密码：`admin123`

首次运行会在 `data/` 下创建 SQLite 数据库、原始体态数据、设备标定、阈值配置、导出目录和导入日志目录。

## 已实现功能

- 登录界面、密码显隐、账号密码校验。
- `DongleAdapter` 加密锁接口；当前使用 `MockDongleAdapter`。
- 用户编号、姓名和手机组合查询。
- 新建、修改、查看、软删除患者档案。
- 自动计算年龄、患者编号唯一校验、手机和邮箱格式校验。
- 不完整批量档案可以入库，但开始检测前必须补齐手机、身高、体重和地址。
- 查看患者历史检测会话，并从用户列表创建新的体态检测会话。
- XLSX/CSV 批量导入、字段映射、重复数据策略、错误明细和导入审计。
- XLSX/CSV/JSON 导出、匿名化、ZIP 打包、manifest 和 SHA-256 校验。
- 其他检测模块已建立导航页和当前患者/检测会话交接入口。

## 体态检测模块

- `DepthCameraAdapter` 相机抽象，包含 Azure Kinect、MKV/NPZ 回放和确定性模拟适配器。
- 相机与 Body Tracking 运行在独立 `spawn` 工作进程；原生 SDK 异常不会直接拖垮 PySide6 主进程。
- 正面、左侧、背面、右侧和 Adams 前屈五姿势状态机，支持单项重拍并保留其他姿势。
- 2 秒稳定窗口硬质量门：单人、1.8–2.2 m、深度和轮廓覆盖、28/32 关节、关键关节、骨盆稳定度、朝向以及 Adams 前屈角。
- Open3D 0.19 可选点云滤波/RANSAC；未安装硬件扩展时使用 NumPy 回放实现，便于 CI 和无相机开发。
- 关节几何、侧面体表样条和 Adams 分层横截面算法；所有体表曲率、侧弯和旋转结果明确标记为“代理值”。
- 测量置信度由深度覆盖、关节完整度、稳定度、拟合残差和跨姿势一致性组成；低于 70% 不输出筛查结论。
- 文献初始阈值位于 `data/posture_thresholds.json`，默认 `validated=false`，因此分级统一显示“实验性/待验证”。
- 人工量尺/直线/水平线/垂线/角度、缩放、镜像、撤销/恢复标注单独写入 `posture_reviews`，不会覆盖自动测点。
- `analysis_ready(session_id)` 已接入“查看报告”占位模块；本阶段不生成 PDF。

## 骨盆体态筛查模块

- 单台 Azure Kinect 按正面、左侧、背面、右侧依次采集；复用设备自检、地面标定、相机工作进程、质量门和重拍归档。
- `FrameBundle` 与 NPZ 回放支持可选的 32 个关节 `wxyz` 四元数；旧回放缺少该字段时仍可使用，但不输出 SDK 模型姿态指标。
- 真机采集前以骨盆、左髋、右髋的位置 RMS 和关节姿态离散度执行稳定预热，处理模式、设备序列号和质量详情写入审计数据。
- 输出髋中心高低差、髋轴冠状倾斜代理角、骨盆中心相对支撑基底侧移、模型骨盆前后倾代理角、模型骨盆水平旋转代理角和背面髋臀区体表对称差代理值。
- 正/背面与左/右侧指标做置信度加权融合；角度使用圆周平均。超过跨视图容差时保留原始视图值并转为“需人工复核”，不会强行平均。
- 采集页仅显示 RGB、深度伪彩、骨架、髋部 ROI 和 SDK 模型代理点；结果页为二维叠加，不生成个体骨骼透视或三维骨盆重建。
- 所有结果统一标记为“实验性/待验证”或“置信度不足”，不显示正常/轻度/中度等医学分级；SDK 模型骨盆角不等同于真实 ASIS–PSIS 临床骨盆角。
- `analysis_ready(session_id)` 向报告模块提供结构化指标、四方向质量、算法版本和免责声明；首版不单独生成 PDF。

骨盆采集数据与五姿势体态目录隔离：

```text
data/assessments/<session_no>/pelvis/
├── front/
├── left/
├── back/
├── right/
└── _retake_history/
```

新增 SQLite 表：`pelvis_captures`、`pelvis_analysis_runs`、`pelvis_measurements`。现有数据库启动时通过 SQLAlchemy `create_all` 补建新表。

体态原始数据结构：

```text
data/assessments/<session_no>/<pose>/
├── capture.mkv
├── calibration.json
├── analysis_calibration.json
├── joints.npz
├── frames.npz                 # 模拟/回放完整窗口
├── raw_frames/                # 真机无原生 MKV 时的逐帧完整窗口
├── analysis_window.npz
├── depth_median.png
├── body_mask.png
├── preview.png
└── manifest.json
```

模拟/回放模式下，`frames.npz` 是完整的可回放 RGB-D 窗口，`capture.mkv` 是明确标记的兼容占位文件；只有原生 SDK 录制器提供真实 MKV 时，manifest 中的 `native_mkv` 才会为 `true`。重拍前一窗口移动到 `_retake_history`，不会被覆盖。所有文件在临时目录写入并完成 SHA-256 校验后才原子提交。

新增 SQLite 表：`posture_captures`、`posture_analysis_runs`、`posture_measurements`、`posture_reviews`。现有 `users` 数据结构未修改，均通过 `assessment_session_id` 关联。

## 相机运行模式

默认 `auto`：检测到 Python 绑定和微软运行库后使用 Azure Kinect，否则进入带明显提示的模拟演示模式。

```powershell
# 强制模拟模式
$env:POSTURE_CAMERA_BACKEND='mock'

# 模拟多人、超距、遮挡、深度空洞等质量门场景
$env:POSTURE_MOCK_SCENARIO='multiple_bodies'

# 强制真机（缺少 SDK/运行库时自检失败，不静默降级）
$env:POSTURE_CAMERA_BACKEND='azure_kinect'

# 回放已保存的会话目录
$env:POSTURE_CAMERA_BACKEND='replay'
$env:POSTURE_REPLAY_ROOT='D:\体态评估系统\data\assessments\AS...'
```

支持的模拟异常值：`multiple_bodies`、`too_far`、`occluded`、`depth_holes`、`unstable`、`wrong_orientation`、`bad_adams`、`garment_artifact`。

## Azure Kinect 真机准备

项目不打包微软闭源 Body Tracking 二进制。部署机需单独完成 EULA/分发审查并安装：

1. Azure Kinect Sensor SDK 1.4.x（提供 `k4a.dll`）。
2. Azure Kinect Body Tracking SDK 1.1.x（提供 `k4abt.dll` 与 DNN 模型）。
3. Python 硬件扩展：

```powershell
& .\.venv\Scripts\python.exe -m pip install -e ".[hardware]"
```

SDK 运行库支持两种放置方式，适配器会按顺序查找（项目内优先，系统目录兜底）：

- 项目内：把 Sensor SDK 的 `k4a.dll` 放到 `tools\`，把 Body Tracking 的 `sdk\windows-desktop\amd64\release\bin\` 整个目录放到项目 `sdk\` 下（含 `k4abt.dll`、`directml.dll`、`onnxruntime*.dll` 和 `dnn_model_*.onnx`）。
- 系统默认安装：`C:\Program Files\Azure Kinect SDK v1.4.1` 与 `C:\Program Files\Azure Kinect Body Tracking SDK`。

注意：k4abt 使用 ANSI 加载 ONNX 模型，项目位于中文目录时模型会自动复制到 `%LOCALAPPDATA%\posture-assessment\azure-kinect\`（ASCII 路径）后加载；首次打开设备会多花几秒完成复制。

Body Tracking 优先 DirectML，初始化失败时自动降级 CPU 并在设备状态中提示。首次使用、设备序列号变化或重新自检时建立 IMU 重力/地面点云 RANSAC 标定档案。Azure Kinect SDK 已停止维护，因此业务代码只依赖通用相机接口，后续可增加 Orbbec Femto K4A 兼容实现。

## 使用边界与验证

本模块只用于体态辅助筛查，不输出疾病概率、Cobb 角或诊断结论。统一采用紧身运动服采集；Adams 背部出现衣物褶皱、反光或深度空洞时必须重拍。

内置阈值在本机构完成 20–30 名受试者、每人至少 3 次的标定/验证分离实验前不可对外作为已验证分级。目标为重复测量 ICC ≥ 0.90、常规角度 MAE ≤ 2°、距离 MAE ≤ 10 mm，Adams 对测斜仪 MAE ≤ 2°且 ICC ≥ 0.85。未达标指标仍可保存数值，但必须继续标记为实验性。

## 上游组件与许可

- [Azure Kinect Sensor SDK](https://github.com/microsoft/Azure-Kinect-Sensor-SDK)：MIT，已归档；微软支持生命周期已于 2024-08-16 结束。
- [Azure Kinect Body Tracking SDK](https://microsoft.github.io/Azure-Kinect-Body-Tracking/release/1.1.x/)：官方闭源运行库，由部署方单独安装和审查许可。
- [pyKinectAzure](https://github.com/ibaiGorordo/pyKinectAzure)：MIT，仅作为可选 Python 真机绑定，不复制其源码或二进制。
- [Open3D 0.19](https://github.com/isl-org/Open3D/releases/tag/v0.19.0)：MIT，可选硬件点云滤波与 RANSAC 运行时。

## 加密锁模拟

默认模拟为已插入。可通过环境变量验证其他流程：

```powershell
$env:POSTURE_DONGLE_STATE='missing'
.\.venv\Scripts\python.exe .\run.py
```

可选值为 `present`、`missing`、`error`。接入真实硬件时实现 `DongleAdapter.check()`，再在应用控制器中替换模拟适配器。

## 测试

```powershell
& "D:\体态评估系统\test.ps1"
```

测试脚本会把 pytest 临时文件和缓存固定到项目的 `tmp/` 目录，避免 Windows 用户临时目录权限异常。
