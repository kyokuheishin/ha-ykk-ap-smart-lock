# YKKApSmartLock for Home Assistant

[日本語](#日本語) · [English](#english) · [简体中文](#简体中文)

## 日本語

YKKApSmartLock は、YKK AP の電気錠を Home Assistant から操作するための非公式 HACS カスタム統合です。YKK AP が提供または承認するものではなく、専用アプリ「スマートコントロールキー」とは独立したコミュニティプロジェクトです。

### できること

- BLE で電気錠を自動検出
- Home Assistant 本体の BLE、または Home Assistant Bluetooth proxy に接続
- ローカル通信による施錠・解錠
- 暗号化された BLE アドバタイズを利用した施解錠状態の同期（手動操作も含む）

### 前提条件

- 専用アプリ「スマートコントロールキー」をインストールしたスマートフォンを、電気錠の管理用スマートフォンとして登録していること
- 設定時に、登録済みの管理用スマートフォンで専用アプリ「スマートコントロールキー」を開き、次の手順を実行できること：
  ［本体設定・鍵の管理］→［鍵の管理］→［スマートフォン］→［追加登録］→［一般用スマートフォン］→［登録開始］
- 登録時は電気錠の近くに Home Assistant 本体または Bluetooth proxy があること

### インストール

#### HACS

1. HACS の **Integrations** でメニューから **Custom repositories** を開く。
2. 次の URL を追加し、種類に **Integration** を選ぶ：
   `https://github.com/kyokuheishin/ha-ykk-ap-smart-lock`
3. **YKKApSmartLock** をインストールし、Home Assistant を再起動する。

#### 手動インストール

リポジトリの `custom_components/ykkap_smart_lock` ディレクトリを、Home Assistant の `config/custom_components/` にコピーし、Home Assistant を再起動します。

### Home Assistant の設定

1. **設定 → デバイスとサービス → 統合を追加** で **YKKApSmartLock** を選ぶ。
2. BLE 自動検出された電気錠を選ぶ。表示されない場合は BLE アドレスを入力する。
3. 登録済みの管理用スマートフォンで専用アプリ「スマートコントロールキー」を開き、上記の手順から［登録開始］を選ぶ。
4. 電気錠から「ピー」と音がしたら、ウィザードで準備完了を確認して続行する。
5. 登録完了後、`lock` エンティティから施錠・解錠を操作する。

### 再登録

管理用スマートフォンで専用アプリ「スマートコントロールキー」を開き、［一般用スマートフォン］の登録をもう一度開始した後、Home Assistant の **開発者ツール → アクション（旧サービス）**から `ykkap_smart_lock.register_device` を実行します。対象の `lock` エンティティを選び、画面の案内に従ってください。

### 注意事項・免責

この統合は非公式で、YKK AP によるサポートや動作保証はありません。対応機種・ファームウェア、BLE の電波状況、Bluetooth proxy の構成によって動作が変わる場合があります。所有者または管理者の許可がある電気錠だけで使用し、導入後は必ず安全な環境で動作を確認してください。緊急時の唯一の解錠手段として使用しないでください。

## English

YKKApSmartLock is an unofficial HACS custom integration for controlling YKK AP smart locks from Home Assistant. It is an independent community project and is not affiliated with or supported by YKK AP or the official YKK AP app.

### Features

- Automatic discovery of the lock over BLE
- Connection through Home Assistant's own BLE adapter or a Home Assistant Bluetooth proxy
- Local lock and unlock control
- Lock-state synchronization from encrypted BLE advertisements, including manual operation at the lock

### Requirements

- The dedicated YKK AP app (「スマートコントロールキー」) has already registered the lock with a management smartphone.
- During setup, the registered management smartphone must be able to follow this path:
  [本体設定・鍵の管理] → [鍵の管理] → [スマートフォン] → [追加登録] → [一般用スマートフォン] → [登録開始]
- Keep Home Assistant or its Bluetooth proxy near the lock during registration.

### Installation

#### HACS

1. In HACS, open **Custom repositories** from **Integrations**.
2. Add `https://github.com/kyokuheishin/ha-ykk-ap-smart-lock` and choose **Integration**.
3. Install **YKKApSmartLock** and restart Home Assistant.

#### Manual installation

Copy `custom_components/ykkap_smart_lock` from this repository to Home Assistant's `config/custom_components/` directory, then restart Home Assistant.

### Home Assistant setup

1. Go to **Settings → Devices & services → Add integration** and choose **YKKApSmartLock**.
2. Select the lock found by BLE. If it is not discovered, enter its BLE address.
3. On the registered management smartphone, follow the path above and select [登録開始].
4. When the lock beeps, confirm that it is ready in the wizard and continue.
5. After registration, use the created `lock` entity to lock or unlock the door.

### Re-registration

After starting registration mode again from the management smartphone, run `ykkap_smart_lock.register_device` from **Developer Tools → Actions** (called Services in older Home Assistant versions). Select the target `lock` entity and follow the prompts.

### Notes and disclaimer

This integration is unofficial and is not supported or guaranteed by YKK AP. Behavior can vary by lock model, firmware, BLE conditions, and Bluetooth proxy setup. Use it only with locks you own or are authorized to manage, and test it in a safe environment after installation. Do not rely on it as the only way to unlock an emergency entrance.

## 简体中文

YKKApSmartLock 是一个非官方 HACS 自定义集成，用于在 Home Assistant 中控制 YKK AP 智能门锁。本项目是独立的社区项目，与 YKK AP 及其官方 App 无关，也不受其支持。

### 功能

- 通过 BLE 自动发现门锁
- 使用 Home Assistant 主机的 BLE 适配器，或 Home Assistant Bluetooth 代理连接
- 通过本地通信上锁和解锁
- 从加密 BLE 广播同步门锁状态，包括在门锁上手动操作后的状态

### 前置条件

- YKK AP 专用 App「スマートコントロールキー」已使用管理手机完成这把门锁的注册。
- 设置时，已注册的管理手机需要能够按以下路径操作：
  ［本体設定・鍵の管理］→［鍵の管理］→［スマートフォン］→［追加登録］→［一般用スマートフォン］→［登録開始］
- 注册期间，请让 Home Assistant 主机或 Bluetooth 代理靠近门锁。

### 安装

#### HACS

1. 在 HACS 的 **Integrations** 中，从菜单打开 **Custom repositories**。
2. 添加 `https://github.com/kyokuheishin/ha-ykk-ap-smart-lock`，类型选择 **Integration**。
3. 安装 **YKKApSmartLock**，然后重启 Home Assistant。

#### 手动安装

将本仓库中的 `custom_components/ykkap_smart_lock` 复制到 Home Assistant 的 `config/custom_components/` 目录，然后重启 Home Assistant。

### Home Assistant 设置

1. 打开 **设置 → 设备与服务 → 添加集成**，选择 **YKKApSmartLock**。
2. 选择通过 BLE 自动发现的门锁；如果没有发现，请输入 BLE 地址。
3. 在已注册的管理手机上按上述路径选择［登録開始］。
4. 门锁发出「ピー」声后，在向导中确认已准备好并继续。
5. 注册完成后，使用创建的 `lock` 实体进行上锁和解锁。

### 重新注册

先在管理手机上再次开启注册模式，然后在 **开发者工具 → 操作**（旧版 Home Assistant 中称为“服务”）运行 `ykkap_smart_lock.register_device`。选择目标 `lock` 实体并按提示操作。

### 注意事项与免责声明

这是非官方集成，YKK AP 不提供支持，也不保证其正常工作。实际行为可能受门锁型号、固件、BLE 信号和 Bluetooth 代理配置影响。请仅用于自己拥有或获授权管理的门锁，安装后在安全环境中确认功能。不要将它作为紧急入口唯一的解锁方式。
