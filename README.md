# 体态评估系统 - 用户信息模块

基于 Python 3.12、PySide6 和 SQLite 的 Windows 桌面应用。目前已完成登录、患者档案、历史检测入口、批量导入、数据导出以及加密锁适配层。

## 运行

项目已经在 `.venv` 中安装依赖：

```powershell
& "D:\体态评估系统\start.ps1"
```

脚本会自动进入项目目录并清除仅用于测试的离屏环境变量，因此可以从任意 PowerShell 目录执行。

默认登录账号：

- 账号：`admin`
- 密码：`admin123`

首次运行会在 `data/` 下创建 SQLite 数据库、导出目录和导入日志目录。

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
