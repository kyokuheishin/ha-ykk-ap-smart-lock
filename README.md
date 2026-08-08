# YKKApSmartLock for Home Assistant

这是一个面向 YKKApSmartLock 的 Home Assistant HACS 自定义集成原型。它让 Home Assistant 主机或已配置的 Bluetooth proxy 作为 BLE Central，提供标准 `lock` 实体。

## 预置条件

1. 先用官方手机完成锁的管理/绑定。
2. 在官方手机上手动打开“普通设备注册模式”。
3. 在 Home Assistant 中通过 HACS 添加这个仓库，或把 `custom_components/ykkap_smart_lock` 复制到 Home Assistant 的 `config/custom_components/`。
4. 在集成配置流程中输入锁的 BLE 地址，勾选“已就绪”。

配置流程会执行普通设备注册链路：可选请求广播密钥 `0x10`，请求锁标识 `0x52`，用 `[0x00]` 请求分配普通 `smartphoneId` 的 `0x51`，最后可选发送退出注册模式 `0x54`。注册结果会保存到配置条目；PIN 不会保存，也不是普通 `0x03` 开锁请求的一部分。

## 目前的实体和服务

- `lock.<name>`：调用 Home Assistant 标准的锁定/解锁服务。
- `ykkap_smart_lock.register_device`：在用户再次手动打开普通设备注册模式后重新注册。

普通锁定/解锁会先发送当前时间 `0x81 0x02`，再发送 `0x80 0x03`。协议 CRC、字段布局和注册响应仍需用授权测试锁的真实 BLE 抓包验证；请勿直接用于生产门锁。
