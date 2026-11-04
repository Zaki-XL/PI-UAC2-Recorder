# PI UAC2 Recorder セットアップ手順書 (SETUP_GUIDE.md)

本ドキュメントでは、初期状態の Raspberry Pi OS から本システム（USB UAC2 ガジェット、Web サーバー、PipeWire、Bluetooth 連携、自動 AP フォールバック、省電力チューニング）を構築する手順を解説します。

---

## 1. 必要なハードウェア・前提条件

本システムは、外部バッテリーや追加電源を持たず、**スマートフォンのバッテリー（バスパワー給電）のみで完結稼働**するミニマル設計です。

| 必要なハードウェア | 要件・推奨仕様 | 役割 |
| :--- | :--- | :--- |
| **Raspberry Pi Zero W** | Rev 1.1（内蔵 Wi-Fi / Bluetooth 搭載） | 音声キャプチャ・Webサーバー・録音制御 |
| **ホスト機器** | USB Type-C ポート搭載（OTG / USB Audio 出力対応のスマホ・タブレット・PC） | バスパワー給電元およびデジタル音声ソース、操作端末 |
| **USB OTG ケーブル** | USB Type-C to Micro-USB（給電・データ通信両対応、SUNGUY 製等） | ケーブル 1 本でバスパワー給電（約 120mA）と 480Mbps 音声伝送を両立 |
| **microSD カード** | 16GB 以上（Class 10 / A1 推奨） | OS、録音データ（WAV）の保存 |

### 初期 OS の準備
1. Raspberry Pi Imager 等を使用し、microSD カードに **Raspberry Pi OS Lite (32-bit / Bookworm 推奨)** を書き込みます。
2. Imager の詳細設定（歯車アイコン）にて以下を設定します:
   - ユーザー名: `pi`
   - ホスト名: `pi-zerow`（任意）
   - Wi-Fi 設定: 自宅の Wi-Fi SSID / パスワード
   - SSH サービス: 有効化（パスワードまたは公開鍵認証）

---

## 2. 一括全自動セットアップ手順

初期状態の PiOS が起動し、ネットワークに接続されたら、以下のいずれかの方法で一括セットアップを実行します。

### 方法 A: Windows PC からの一括実行（推奨・最も簡単）
PC と Raspberry Pi が同一のローカルネットワーク（自宅 Wi-Fi 等）に接続された状態で、PC の PowerShell から以下を実行します。

```powershell
powershell -ExecutionPolicy Bypass -File setup_to_pi.ps1 -ip 192.168.1.186
```
> ※ `192.168.1.186` の部分は、お使いの Raspberry Pi の IP アドレスに置き換えてください。

**【自動実行される内容】**
1. 必要ファイル（ソースコード、WebUI、セットアップスクリプト）を SCP で自動転送
2. 必要パッケージ（ALSA, PipeWire, BlueZ, NetworkManager, Python3 等）の自動インストール
3. カーネルブート設定（DWC2 ガジェットドライバ、`otg_mode=1`）の自動追記
4. systemd サービス（`usb-gadget.service`, `recorder-web.service`）の自動登録・有効化
5. PipeWire 音声スタックおよびユーザーセッション（`linger`）の自動構成
6. 緊急アクセス用 AP（`Pi-Recorder-Hotspot` / 優先度 -10）の自動プロファイル登録
7. 省電力＆ブート高速化（LED 完全消灯、不要サービスのマスク）の自動適用
8. セットアップ結果の自動検証（全 9 項目のセルフチェック）

---

### 方法 B: Raspberry Pi 上で直接実行する場合
Raspberry Pi に SSH 接続後、本リポジトリを展開し、以下を実行します。

```bash
cd pi-uac2-recorder
sudo bash scripts/setup_all.sh
sudo reboot
```

---

## 3. 設定検証・ヘルスチェック (Health Check)

セットアップが正常に完了し、すべてのサブシステムが正しく稼働しているかをいつでも検証できます。

### PC から検証する場合:
```powershell
powershell -ExecutionPolicy Bypass -File verify_pi_setup.ps1 -ip 192.168.1.186
```

### Raspberry Pi 上で検証する場合:
```bash
bash /home/pi/verify_setup.sh
```

### 検証項目一覧（全 9 項目・すべて PASS で合格）

```text
================================================================
   PI UAC2 Recorder システム状態・設定完了検証                  
================================================================
[1/9] カーネルブート設定 (dwc2 / otg_mode)... PASS
[2/9] USB UAC2 ガジェット (48kHz / 16bit 2ch 標準オーディオ)... PASS (ALSA PCM に UAC2Gadget 認識済)
[3/9] Web サーバーサービス稼働状態 (recorder-web)... PASS (active running)
[4/9] Web API 応答確認 (http://localhost:8080/api/status)... PASS (200 OK 正常応答)
[5/9] PipeWire 音声サブシステム (pw-record / loopback)... PASS (PipeWire インストール済)
[6/9] Bluetooth スタック (bluetooth.service / BlueZ)... PASS (Bluetooth サービス active)
[7/9] 自動APフォールバック設定 (Pi-Recorder-Hotspot)... PASS (登録済 / 優先度 -10)
[8/9] 実行スクリプト & 静的ファイルの整合性... PASS (全ファイル配備完了)
[9/9] 省電力 & 高速起動チューニング (LED消灯 / 省電力スクリプト)... PASS (LED消灯・最適化スクリプト適用済)
================================================================
  【総合判定: 合格】 9 / 9 項目が完全に正常稼働しています！
================================================================
```

---

## 4. トラブルシューティング

### Q1. スマホを接続しても USB 音声機器として認識されない
- **原因 1: ケーブルの向き・仕様**:
  - ホスト（スマホ）側に OTG 端子（Type-C）が挿さっているか確認してください。
  - 充電専用ケーブルでは認識されません。データ通信・OTG 対応ケーブル（SUNGUY 等）をご使用ください。
- **原因 2: 接続ポートの間違い**:
  - Pi Zero W には 2 つの microUSB ポートがあります。**内側（USB）ポート** に接続してください（外側の PWR IN ポートは空きにします）。
- **原因 3: USB ガジェット未起動**:
  - WebUI のクイックバーにある `[🔄 USB再接続]` ボタンを押下するか、SSH で `sudo /home/pi/setup-usb-gadget.sh` を再実行してください。

### Q2. 外出先で WebUI に接続できない
- 自宅 Wi-Fi 圏外では、約 30 秒〜1 分で自動的に AP 親機（`Pi-Recorder-Hotspot`）として立ち上がります。
- スマホの Wi-Fi 設定から `Pi-Recorder-Hotspot`（パスワード: `recorder1234`）に接続し、ブラウザで `http://192.168.4.1:8080/` にアクセスしてください。

---

## 5. ⚠️ Google Pixel シリーズをサポート対象外としている理由

本システムは、標準的な USB Audio Class 2.0 (UAC2) に準拠した一般的なスマートフォン（Galaxy、Xperia、AQUOS、Xiaomi、OPPO、iPhone 15 以降の Type-C モデル、PC 等）で幅広く動作しますが、**Google Pixel シリーズ（Pixel 6 / 7 / 8 / 9 等）はサポート対象外（非対応）**としています。

開発・実機検証の過程において、Pixel シリーズ固有の以下のハードウェア・OS スタック上の制約が確認されたためです。

### 1. USB Type-C / PD コントローラの超厳格なポート遮断
- Google Tensor 搭載の Pixel は、USB-C コントローラおよびカーネルの規格判定が一般的な Android 端末と比較して極度に厳格です。
- Linux ConfigFS による仮想 USB ガジェット（DWC2）との通信において、速度ネゴシエーション（High-Speed / Full-Speed）やディスクリプタ属性のわずかな遷移タイミングのズレを「規格違反」と判定し、端末側が Type-C ポートの通信および給電を一方的に強制遮断（Speed: -1 状態へ遷移）する挙動が多発します。

### 2. Isochronous 転送における過剰なクロック同期・スロットリング (HF Overflow)
- Pixel のオーディオ HAL（AudioFlinger / Google Tensor DSP）は、USB Audio の等時性（Isochronous）パケットに対して極めて狭い許容範囲しか持っていません。
- Pi Zero W 側の USB コントローラで生じる微小なパケットジッターやクロックドリフトを許容できず、端末側で `HF overflow`（Isochronous 要求の破綻）を引き起こします。これにより、スマホ側で音楽や動画の再生速度が極端にスローになったり、音声出力自体がフリーズ・遮断されます。

### 3. バスパワー給電時の過電流保護（OCP）の誤作動
- Pixel の Type-C ポートは、ホスト動作時の外部給電（VBUS 逆給電）において突入電流や電圧降下に非常に過敏です。
- Pi Zero W 起動時の瞬間的な電力需要（Peak 150〜200mA）や電圧の揺らぎを検知した際、端末の過電流保護回路が即座に給電を遮断してしまい、起動途中で Raspberry Pi の電源が落ちる現象が発生します。

### 4. 物理抜去を伴わないソフト再認識の拒絶
- 一般的な Android 端末であれば、ソフトウェア的に D+ プルアップ抵抗を切断・再接続することで物理的なケーブル抜き差しをエミュレート（再認識）できますが、Pixel シリーズは一度エラーや遮断が発生すると、**物理的に Type-C ケーブルを完全に抜いて数秒間待機しない限り、USB ホストモードを再開しない**という制約があります。

> **推奨動作環境**:
> 安定した 120mA 給電と標準 UAC2 デジタルキャプチャを行うため、Galaxy、Xperia、Xiaomi などの一般的な Android 端末、または iPhone 15 以降（Type-C 搭載モデル）、PC 等でのご利用を推奨します。
