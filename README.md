# Neko Video

## 1.7.0：可选本机 DTK 媒体解析

管理员在WebUI「看图与发送媒体」选择`media.provider="dtk"`后，B站和抖音显式`neko_video_open`使用固定`http://127.0.0.1:25526`的DTK 5.1.11 API（源码`49f4a9730b478c5f56d843a1c033e0960814aaaf`）。默认`legacy`仅保留原抖音来源，不自动切换线上配置。

- DTK控制台中已导入的游客Cookie由DTK自行管理，用于显式open取源。插件不读取、复制或输出它们；管理员另建viewer角色、仅具`douyin:read`和`bilibili:read`权限的API Key，在**本插件私有data_dir**创建`dtk-api-secrets.json`，唯一字段`api_key`填写Key，目录0700/文件0600。普通任务轮询不需额外scope，不指定identity，不请求管理权限。不把Key写入仓库、聊天、日志或普通工具参数；该文件不能用Cookie替代。API地址不能由模型改写，也不支持任意内网地址/远端地址。
- 使用方式：发B站/抖音链接后要求“看看这条视频/图文”，open取得本聊天编号，再显式analyze或send。共同受plugin.enabled、media.enabled控制；parse.enable_douyin只控制抖音来源，B站及聊天视频独立。分析/ASR沿原配置，不因切换来源开启。
- 每次解析只提交一次：抖音`POST /api/v1/parse?wait=0`，B站`GET /api/v1/bilibili/video?wait=0&part=N`。202仅轮询同一个UUID；最多25秒/25次查询，仍在原open总期限内。失败/404/超时/取消不重新提交、不切旧供应商。取消仅停止本地等待；DTK可能已运行或与其他调用合并，因此不DELETE共享任务，不能承诺撤销远端工作。后续独立显式open是新调用，DTK自身可复用缓存；插件不承诺跨调用/重载解析幂等。
- 返回图片、实况和视频复用现有公网IP固定、URL/重定向/大小/容器校验及会话编号、12帧、ASR、永久unknown账本。API鉴权客户端和公网媒体客户端分离，Key/Cookie不发给媒体CDN。DTK失败信息仅固定脱敏文字，不返回原始响应、任务meta或raw。
- **B站选P**：BV/av号及无p参数的完整video链接默认P1，完整链接`?p=N`选择第N个P；本次P/总P数写入open正文，不把单P说成完整合集。重复、空、非正数p拒绝，不存在的P不回退第一P。短链仅接受无参数且返回单P的视频，多P短链要求换完整video链接明确选P，避免隐藏跳转选错P。仅取DTK5.1.11规范化`media.video.format=mp4`的单段渐进流；缺失流、DASH分离音视频、分段流、直播/番剧等明确拒绝，不合并或伪装完整视频。B站CDN仅附公开Referer/User-Agent，原公网下载限制保持。
- info及自动文字参考仍使用原官方资料链，DTK里的B站Cookie不会供给这些调用；B站手记插件独立。未增加TikTok等平台、搜索、评论、DTK下载任务、浏览器或DTK ASR。后续抽帧/ASR依旧只对当前聊天登记的视频编号显式执行。

仅合成HTTP和本地SDK回归，不代表DTK游客权限、真实平台下载、模型理解或QQ投递已验收。部署和凭据配置须独立确认。

1.6.0审查修复源码：续读/截断/来源说明进入模型实际消费正文；原生video与file统一真实消息/群私对端/账号校验；聊天视频独立于抖音开关，抖音来源撤权仍生效。MP4及带ftyp的QuickTime MOV沿原受限解码管线；不支持MKV/AVI/FLV/WebM。自动资料改为来源绑定的请求前临时参考，不修改原消息。分析与发送未决状态持久保留，不因TTL、换编号或重载解除；分析哈希绑定发送内容。本地源码不代表已部署或QQ/下载/模型实测。

### 1.6.0数据与回退边界

- `analysis/attempts.db`的user_version升级到1，原四列及身份映射无损保留，旧五列仍事务迁移。所有尚存unknown（含超过一小时及无身份映射的旧记录）继续阻挡；无映射旧unknown保守阻挡全部分析。旧版本已物理删除的记录无法恢复或追认。
- 只有成功结果按一小时缓存TTL清理；未决记录不自动到期。分析与发送各最多128条，满则拒绝，不淘汰unknown。新`delivery/attempts.db`按真实聊天＋类型＋下载内容SHA256防重，发送前落unknown，只有明确成功才解除该次准入，允许之后有意重复分享；False/None/异常/取消不清账。
- 1.5.1以前的发送状态只在内存，升级无法重建已消失的未决发送；不重放历史任务。没有新增模型可调用的清账/审批接口。
- **禁止裸退1.5.1或更早版本读取新账本**：旧代码会过期删除unknown且忽略发送账本。回退须兼容修补构建保留永久未决/发送保护，或保持分析和媒体发送关闭；不可恢复旧数据库抹掉新unknown。user_version只是新读取器的版本检查，不能阻止不检查版本的旧代码。
- 自动参考仅128条/15分钟、单条约3000字符、每次最多4条/6000字符；绑定真实session/message，使用Host首段前缀或Replyer权威目标，别名冲突/组外来源跳过；Focus仅从reply Schema枚举确定可用会话。关闭对应群/私自动资料、抖音开关及卸载清理生效，不写原用户正文/数据库/学习输入，不能承诺后续自然回复从不影响摘要。
- Host存在结构化结果兜底，但已有非空content时普通和Focus历史优先正文；分页合同因此直接写正文。`scripts/verify_host_consumption.py --host-source <匹配Host源码> --temp-dir <隔离临时目录>`补充验证实际转换/历史追加、兜底条件及前缀序列化往返；原`verify_host_runtime.py`的真实Runner加载/SDK/生命周期验证保留。
- 2026-11-06仅检索公开文档及现有来源，未找到固定HTTP解析供应商的可核实HTTPS地址/证书/等价接口证据；未进行供应商探测，保原地址而非盲改HTTPS或换供应商。明文篡改风险仍在，可用现有`parse.enable_douyin=false`停用该来源，不影响聊天视频。

**让麦麦看懂分享，而不是只看见一条链接。**

为 MaiBot 提供 B站/抖音资料读取、图文预览、聊天视频/视频附件取源、显式采样理解与原媒体发送。1.5.0将五工具整理为info/open/analyze/send四个入口；默认采样12帧（设置1–24），分析时按ASR配置默认转写第一音轨，模型不再填音轨开关。打开/登记媒体不自动分析或发送，未知防重与管理员分析/ASR开关保持。

2026-10-25维护部署1.4.5后，用户确认聊天视频功能可用；这不是所有来源、音轨质量和转发结果的全面验收。
先前持续“找不到消息”已证实为SDK解包层级误用，不是已证实的Focus错位；无需为此迁移宿主或更换模型。

[下载安装包](https://github.com/RoselindLabs/neko-video/releases/latest) · [安全与隐私](SECURITY.md) · [更新记录](CHANGELOG.md)

## 使用起来

- **分享视频**：自动补充标题、作者和平台可提供的总结、章节等文字资料。B站摘要临时不可用或平台未提供时尝试字幕，单独标注摘录/抽样范围；失败、未登录和没有摘要分别说明，不冒充看过视频。
- **“看看这条视频”**：`neko_video_open(source_message_id=真实消息ID)`登记当前聊天视频或视频文件附件，返回编号供分析或发送。
- **“看看这篇图文里的图片”**：麦麦按需读取抖音图片，每次最多4张，可以继续看后面的图片。需要支持视觉的Planner模型。
- **“把第2张原图发给我”**：按本聊天里的媒体编号发送选中的原文件，不拿压缩预览充当原图。
- **“发这个视频/实况”**：流式下载、校验容器后交给本机QQ上传，不再把整个视频塞进RPC，因此没有旧8MiB门槛。不会自动下载整套图集。

> info只读平台文字；open只预览/登记。管理员开启分析后，模型显式调用analyze才抽帧：默认12、设置1–24，短片可少于设置值。分析按管理员ASR开关默认转写第一音轨，无音轨不调用voice；模型没有include_audio参数。不接受任意URL或服务器路径。

聊天入口读取实际OneBot video/file段，以及宿主持久化的group_upload通知；附件核验真实消息、群私聊和QQ账号，分别调用群/私文件地址接口。不读普通文档/压缩包、不自动追溯引用或列群目录。分析仍限现有MP4/MOV，不代表全部编码都能解码。1.7起B站原视频仅在DTK模式下接入，选P限制见上方；任意网址取源不支持。

### 四个短提示工具

| 工具 | 用途 |
| --- | --- |
| `neko_video_info(link)` | 自动识别B站/抖音，读取平台文字资料、摘要/字幕/章节 |
| `neko_video_open(link?,source_message_id?,image_start=0)` | 链接与真实消息ID二选一，预览平台媒体或登记聊天视频/附件；B站需DTK，?p=N选P |
| `neko_video_analyze(media_id,offset=0)` | 显式分析已有编号，按配置默认带音轨；offset续读成功缓存 |
| `neko_video_send` | 在QQ发送选中的原图、小视频或实况 |

自动处理只登记**文字资料临时参考**，请求前按来源注入，不改用户原消息，不会自动分析、发QQ或逐条调用第三方媒体接口。说明每项≤45字、四项合计≤190字。旧bilibili/douyin/read/chat处理方法只保内部兼容，不另注册给模型。

### B站摘要与字幕

沿用本插件独立登录和`parse.enable_ai_summary`：开启后先取平台AI摘要；`code=-504`表示服务调用超时，不等于账号/权限被拒绝。临时网络/服务异常或无有效摘要时，尝试当前单P视频字幕，优先中文。登录/权限/风控/限流失败不换接口绕过，未登录或开关关闭不读字幕，多P暂不回退以免误认覆盖范围。不借用B站自主插件的凭据/名单/数据库，也不启动另一套自动浏览。

字幕文件只允许官方HTTPS域，公网DNS固定、独立无账号Cookie客户端、不跟随重定向，最多2MiB/3秒；最多24条时间片段、约4500字正文，明确不是AI总结、没有画面/音频。只有元数据的结果最多缓存30秒短退避（不自动后台重试），成功正文仍按配置缓存。原自动附加9秒/工具40秒总期限保持，慢请求仍可能超时跳过；此功能不调用模型、不下载原视频或触发ASR。

## 安装与启用

要求 **MaiBot ≥1.3.9、SDK ≥2.12、Python ≥3.12**。

1. 从[Release](https://github.com/RoselindLabs/neko-video/releases/latest)下载ZIP，按同页`checksums.txt`核对SHA256。
2. 解压到`plugins/neko-video/`，确保`plugin.py`、`_manifest.json`和`neko_video/`在同一插件根目录；由宿主安装manifest声明的依赖。
3. 在WebUI检查解析范围、凭据及管理员名单，然后启用。首次安装**默认关闭**，不是安装后自动开始解析。
4. 需要图文看图时，开启`parse.enable_douyin`和`media.enabled`，并确认Planner支持视觉。

插件ID为`neko.video`，不是旧内容理解插件的同名更新。不要同时启用两个插件的自动附加。项目不会自动搬运旧Cookie或删除旧数据；迁移时先备份，再将适用配置和凭据复制到新插件的独立数据目录。

## 配置

通常在WebUI修改即可。以下为简化示例，**不要直接覆盖已有配置**：

```toml
[plugin]
enabled = false
config_version = "1.2.0"

[parse]
enable_ai_summary = true
enable_in_group = true
enable_in_private = true
enable_douyin = true
cache_ttl_seconds = 1800

[permission]
admin_users = [] # 填可信发送者ID；空名单禁止登录管理命令

[media]
enabled = true # 仍受总插件开关及enable_douyin控制
fallback_token = "" # 可用服务器私有凭据文件代替
video_max_mib = 256 # 单视频上限；0为不限，仍受磁盘/缓存/超时及QQ限制

[analysis]
enabled = false # 独立付费分析开关，默认关闭
max_mib = 64 # 分析独立上限，不受发送video_max_mib=0影响，最大64
max_seconds = 120 # 超长明确拒绝，不截前半段冒充完整理解
frames = 12 # 可设1–24，短片不凑帧数
timeout_seconds = 90 # 总期限含排队/下载/解码/模型等待
model_timeout_seconds = 35 # 模型等待，不自动重复请求

[asr]
enabled = true # 分析默认转写第一音轨；关闭则只看画面，模型不填写音轨参数
timeout_seconds = 35 # ASR等待上限；仍在analysis.timeout_seconds整轮期限内

[credential]
sessdata = ""
bili_jct = ""
buvid3 = ""
dedeuserid = ""
douyin_cookie = ""
```

### 登录与凭据

| 命令 | 用途 |
| --- | --- |
| `/neko-video login` | 管理员扫码登录B站 |
| `/neko-video status` | 管理员检查B站登录状态 |
| `/neko-video logout` | 管理员清除本插件扫码凭据 |

- 管理员名单为空时拒绝以上命令；不根据“最近说话的人”猜身份。名单不限制模型的普通资料工具使用。
- B站扫码凭据保存在本插件`data_dir/credential.json`，优先于配置Cookie。登出只清扫码文件；配置仍有Cookie时会明确提示继续清理。
- 抖音Cookie只用于官方接口。游客标识可存于`douyin-visitor.json`，不会把账号Cookie发给第三方解析器。
- 备选媒体接口Token放在`media.fallback_token`，或`data_dir/douyin-media-secrets.json`的`fallback_token`字段，后者应0600。**不要提交Token、Cookie或扫码链接到GitHub。**

## 显式视频理解（1.2.0起，1.3.0可选ASR）

先用`neko_video_open(link="抖音分享链接或DTK模式下的B站视频链接")`或`neko_video_open(source_message_id="真实视频/附件消息ID")`取得编号，再：

```text
neko_video_analyze(media_id="本聊天视频编号")
```

分析只填media_id与可选offset，不填link/image_start/analyze_video/include_audio；不把封面编号当视频。正文较长时按正文提示或next_analysis_offset续读，只读成功缓存，没有缓存不新调模型。编号仍按聊天隔离/10分钟过期，不消耗或重置发送状态，不重发未知媒体。图文预览、Cookie/Token/供应商保持，自动资料改为独立临时参考。

- 每次仅使用编号中第一个已解析资源地址，不自动换URL/供应商。沿用公网DNS固定/逐跳地址校验、下载字节/磁盘余量/缓存上限；分析文件在独立`analysis/video-spool`，完成后删除，异常残留仍受原spool额度及TTL约束。
- ffprobe检查有限时长/流数量/尺寸/≤1678万原始像素；仍仅MP4/MOV输入。ffmpeg默认12帧、配置1–24，每帧长边≤960/180KiB，最多24帧总4320KiB，Base64后仍受宿主16MiB边界。短片不凑帧数，近似seek不是精确PTS，不覆盖未采样动作/剧情。私有受控文件、参数数组、file/pipe及Linux资源/取消保护保持，不是OS文件系统沙箱。
- 最多4个在途/排队请求、单分析槽、排队也计总deadline。结果缓存按视频SHA256＋聊天＋分析/音频选项隔离；未知请求防重只按聊天＋视频SHA256，不受超时、采样或音频选项变更影响，且优先于旧成功缓存。同编号同配置的成功结果直接读缓存，换编号相同字节与选项也不再请求模型，跨聊天不共享内容。当前宿主模型参数变更不主动清成功缓存，回执明确`cache_hit`。
- 不要求音频时最多**1次SDK llm.generate调用**，既有vlm任务、2500输出tokens，不传整段聊天/人设。显式ASR最多额外1次宿主llm.transcribe_audio，见下节。宿主自身模型切换/供应商重试策略未被插件改写，因此不是“上游绝对仅1次”的保证；取消RPC也不保证上游撤销或退费。
- 模型调用前SQLite写入unknown准入，失败/超时/取消/重载后均不自动重放；成功缓存按创建时间保留1小时，unknown不自动过期，最多128条。队满/损坏DB明确失败且不删库；成功结果单条≤32KiB、完整模型正文≤24000个UTF-8字节，单次最多输出6000字符并在正文提供缓存续读，不悄悄丢尾部。目录0700/记录0600，DB最多2048页，记录仅聊天/内容/选项摘要key及分析结果，不保存原URL/视频。
- 1.3.1保留原四列`analysis`表，以独立`analysis_identity`表记录防重身份，两表同事务准入。过渡五列表会在单事务中复制全部原记录和身份、恢复四列；任何失败完整回滚，映射冲突/额外索引或触发器拒绝自动迁移，不删库。旧源码四值INSERT仍兼容，但**不具备新版防重或时钟保护语义**；正式安全回滚必须使用兼容修补构建，不能直接回退旧源码作为安全方案。
- 旧版未知key无法反解聊天/内容，保守阻止所有分析（包括成功缓存读取），不会按时间解除；新版未知记录仅阻止相同聊天/内容。改配置、时间经过或重载不会解除。旧源码新增的无映射记录亦按旧未知处理，需保留现场离线核查。
- 所有缓存读取/准入/完成前校验`created`：NULL（包括SQLite存入NaN）、非数值、非有限数、负值或超过当前墙钟5秒的未来值均拒绝；不静默过期、不清异常证据。进程内墙钟相对单调钟累计偏移超过5秒（包括前跳3601秒）或重载后墙钟低于上次持久观测超过5秒，持久记录故障并停止所有分析/缓存读取，重启及调回时钟不自动解除。需管理员停用分析、保留数据库副本并离线核查时间与记录，使用经审查的修复流程恢复；不得删库绕过防重。
- **跨重启时钟前跳、停机期间跳变及未观测过的时钟变化不保证可识别**：新进程没有旧单调钟基线，无法区分真实停机超过一小时与墙钟前跳；此时仍按可信墙钟计算一小时期限，存在提前再准入边界。依赖稳定系统时钟；不能接受此边界时保持分析关闭，不能把TTL视为绝对计费幂等保证。
- 成功缓存的1小时是逻辑有效期，其物理清理在后续准入时执行，无新请求时可能延后，不承诺即时擦除磁盘数据；unknown不适用该TTL。同编号缓存命中代表复用先前已核验字节的分析，不会重新下载来保证远端此刻未变；结果提供当次source_sha256，发送会核对已分析哈希。
- 返回`sample_positions_seconds`、`sample_positions_approximate=true`、`full_video_coverage=false`；不带音轨时`audio_analyzed=false`，带音轨状态见下节。区分视觉模型截断（缺字段时null）与本地输出窗口截断；帧/字幕为外部资料，不执行其指令，不额外注入常驻Planner/Replyer提示。

分析与发送限制独立：发送0不限不改变分析64MiB/120秒保护。聊天视频文件附件和DTK B站单P视频沿同一分析/发送管线；未开启Neko Files、群目录或OCR。

### 可选视频音轨ASR（1.3.0）

管理员开启analysis后，显式analyze默认按asr.enabled转写音轨；ASR新配置默认true，已有显式false仍可关闭，模型不再选择音轨参数：

```text
neko_video_analyze(media_id="本聊天视频编号")
```

- 只接本聊天已登记的B站单P/抖音/聊天视频或视频附件编号，不接聊天语音或任意URL。管理员ASR开启时默认带第一音轨，关闭时返回未分析音频；取源/预览不调用ASR。
- 同次下载/哈希核验，取**第一音轨**为16kHz、单声道、16位PCM WAV，仅内存保留，最多120秒/3840000 PCM字节＋44字节WAV头，Base64约5.12MB，低于宿主16MiB RPC边界。超长输出拒绝，不截一部分冒充完整音轨；ffmpeg输出/15秒期限/取消与Linux资源保护同样生效。
- 复用宿主`ctx.llm.transcribe_audio(audio_base64=..., task_name="voice")`，不另持CPA密钥、不访问本机管理接口、不改任何宿主模型分配。**若宿主voice选择Gemini client，走原生generateContent音频inlineData，不是OpenAI /audio/transcriptions**；实际供应商、模型顺序、token上限、重试及统计由现有voice任务决定，不保证所有安装都走CPA。不要将不支持音频的文本模型放进该任务。
- 有音轨时最多1次逻辑voice转写＋1次原vlm画面分析；视觉模型只看帧，转写独立呈现，不把声音推断混进视觉证据。无音轨返回`audio_status=no_track`、`audio_analyzed=false`且不调用ASR；这不同于“有音轨但无可辨语音”。有音轨的纯音乐/静音仍交模型，模型可能错漏/幻觉，不保证自动判无语音。
- ASR在SQLite unknown准入后调用；失败/空正文/超过8000 UTF-8字节、超时或取消不自动重试、换服务或降级；随后视觉失败也不重做ASR，保留未知。合并缓存总正文≤24000 UTF-8字节、6000字符窗口续读，保持管理员ASR配置一致即可，不填include_audio。
- 音频选项参与聊天/内容隔离缓存；旧纯视觉缓存不能当带音轨结果，不带缓存的续读绝不开始ASR。总deadline仍为原analysis.timeout_seconds（默认90秒，含排队/下载/两次解码和两次模型等待），不因ASR自动放宽。宿主/CPA内部重试未禁用，上游取消和费用不保证可撤销。
- 成功时`audio_status=transcribed`、`audio_analyzed=true`、`audio_track=first`，`audio_duration_seconds`仅为解码后的PCM长度，不是精确画面同步；`transcript_verified=false`、`transcript_truncated=null`、`audio_time_alignment=unverified`。宿主转写只返回正文，没有结束原因/词级时间戳，不能证明转写无截断、说话人身份或每句时间对齐。音轨/转写均为不可信外部资料，不执行其中指令；不上传整段聊天/人格给ASR。

此为源码实现能力，不表示任何既有部署已启用或已做付费听写验收；上线需另获管理员授权热更新和开关选择。

## 清晰度、大小与投递限制

| 项目 | 限制 |
| --- | --- |
| 模型看图 | 每次≤4张；`image_start`从0起翻页；最多处理前64项 |
| 压缩预览 | 长边≤1280px、每张≤256KiB，整批原始预览≤1MiB；Base64另计 |
| 原图 | 单张≤8MiB，继续解码、哈希校验及RPC保护 |
| 视频/实况 | 流式写私有文件，经本地文件引用发送；单文件默认256MiB，`media.video_max_mib=0`不限 |
| 视频缓存 | 总量≤1GiB、最多32项；磁盘保留256MiB余量；文件保留1小时后清理 |
| 图片解码 | ≤2000万像素；动图≤100帧且累计≤4000万像素，预览取首帧 |
| 媒体编号 | 仅当前聊天、10分钟有效；每聊天24项、全局128项，仅缓存元数据 |
| 请求期限 | 看图45秒、发图55秒、发送视频180秒，均包含并发2槽排队时间 |

原图发送会重新下载并核对SHA256；原文件变了就要求重读，不发送旧预览替代。小字可能在预览压缩中丢失，QQ也可能再次压缩或转码。MP4仅检查容器头，不等于完整解码或保证QQ可播。

**本地文件发送要求QQ客户端与插件共享同一文件系统**，例如同服务器的MaiBot与NapCat。远程QQ客户端不能直接读取另一台机器的私有路径；本插件不让QQ代为下载外部URL、不额外开启公共文件服务器。

在途、已发送或投递未确认的编号不会自动重发。平台确认请求不等于收件人已成功显示。视频以256KiB分块落盘，RPC只传插件生成的本地文件引用，不传外部地址或整段Base64，不保存到聊天数据库或模型历史。成功或未知文件暂留1小时，避免平台未读完就删掉；每5分钟及下次启动/下载时只清本插件过期文件。取消或下载失败会删除未完成文件。QQ仍可能限制大小、时长、编码或转码；`0`不代表承诺任意大小都能发送。宿主16MiB边界不变。
配置更新/卸载会取消在途请求并清理媒体编号；保留缓存至过期，不删除普通聊天历史。

## 第三方接口与隐私

官方资料与第三方媒体解析使用不同客户端，后者只接收公开分享链接或视频ID，**不接收B站/抖音账号Cookie**。第三方接口不属于本项目，可能失效、限流或返回不完整内容。

当前主媒体解析接口使用**明文HTTP**，沿途可能读取或修改请求和响应；只用于公开内容。不能接受该信任模型时关闭`media.enabled`，保留官方文字资料功能。下载媒体时会校验并固定公网IP、逐跳检查重定向，拒绝内网、凭据URL及非80/443端口；这些防护不等于认证第三方返回内容。详见[SECURITY.md](SECURITY.md)。

2026-10-20 匿名小请求复核：同IP的8008端口不能完成TLS握手；443端口证书过期，不能证明与现有解析服务同源或兼容。HTTP两条路由仍响应各自的必填字段校验。未绕过证书验证，未证实HTTPS的跳转/视频完整响应合同，故不擅自改协议或供应商；**明文链路风险仍未解决**。

## 开发与来源

SDK接入、配置、官方解析、凭据、存储、展示和媒体投递分别位于`neko_video/`中的独立模块；`plugin.py`仅负责宿主发现。测试使用模拟网络和合成媒体，不登录账号、不发QQ、不调用模型。

```bash
python -m pip install -e . ruff
python -m unittest discover -s tests -t . -q
python scripts/test_strict.py
ruff check .
ruff format --check .
python scripts/build_release.py
python scripts/verify_release.py dist/neko-video-1.3.1.zip
```

日常源码CI用Linux生产Python3.14.4/SDK2.16.0与Windows最低Python3.12/SDK2.12两组；原8组完整兼容保留在v标签、手动full及有源码变化的夜间验证，另保留固定生产组合。保留完整自有测试、关键媒体/回执/持久防重/打包测试组实际发现、真实ffmpeg/ffprobe烟测和lint/format，不用固定测试总数证明正确；零发现、任何skip、失败或导入错误仍失败。每档最终ZIP仍核摘要/白名单、隔离SDK发现4工具并导入全部包内模块，检查来源必须是解压目录，负向缺模块测试仍拒绝回退源码。`verified-*`包含已验ZIP/checksums并保留14天，不自动Release或部署，Actions原固定SHA不变。纯根目录/docs下Markdown只查文档，不获得源码发布资格；源码上传须同main提交及核心测试/包验证/ci-gate/publish-gate成功，受控手动同HEAD核心验证可用于文档HEAD发版，不做空提交或调用真实模型/QQ。

本项目为独立仓库，保留派生代码的作者和来源说明，不把独立建库等同于全部原创。采用MIT许可，见[LICENSE](LICENSE)与[NOTICE](NOTICE.md)。
