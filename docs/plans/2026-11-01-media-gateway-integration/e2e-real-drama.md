# E2E-real：drama-production 短剧链路（R4 第三关）

> 2026-11-02 执行。真实 MGW `http://172.17.0.1:8000`（users 模式）+ dev 栈
> `:2027`（Gateway :18001）；触发方式：dev 后端 API 登录
> `admin@dream.com` → 建会话 → 发消息（auto 模式最小任务：1 章 1 片段 2 镜
> ×5s《深圳湾日出》）。R4 顺序：provider → console-link → **drama（本文）**
> → video-clips → t2i/image-post → content-series。

## 一、直连网关冒烟（不经 skill）

| 步骤 | 结果 |
|---|---|
| `GET /health` | ok；`auth_mode=users`；引擎 t2va(:30020) 不可达，comfy(:8190) 可达（fl2va/ref2va） |
| `GET /v1/images/modes` | 24 档全部返回；`t2va` 系（h3-t2v/h3-clip-*/h3-frame-*）已被管理员禁用（白名单不含 t2va），`POST` 直接 **403 `gen_type_disabled`**（可读、retryable=false） |
| 生图 `POST /v1/images/generations`（qwen-image-std） | **202** → 轮询 ~45s **completed**；产物 `data[].url=/v1/images/jobs/{id}/file?...` + b64 |
| 生视频（i2v/ref 路径） | `h3-i2i-std`：**202 后 failed** —— MGW↔ComfyUI 自定义节点契约不匹配：`MiniMaxH3ImageToVideo.execute() got an unexpected keyword argument 'audio_vae'`（网关侧 bug，见问题清单 G1）；`h3-ref-std`（ref2va）：202 → ~14.5 分钟 **completed**，`output=video` 产物为 2.6MB 合法 mp4（`kind=video`） |
| 取消 `DELETE /v1/images/jobs/{id}` | 运行中 → **409 `running_no_cancel`**（提示 force=1）；`?force=1` → 200 `{"cancelled":true}`，作业终态 `cancelled`，dressup 台账同步置 failed |
| 幂等 | 同 key 重复提交返回 202；错误信封 `{"error":{code,message,retryable}}` 全程一致 |

**时长假设修正**：`eta_s`（155s）显著低估 ref2va 实测（冒烟 14.5 min；
E2E 双参考图 10s 片段 ~31 min，99% 停留占大半——视频编码阶段引擎不报进度）。
skill/前端轮询预算应按 **30–40 min/笔** 上限设计。

## 二、skill E2E-real（dev 后端全链路）

会话 thread `3b897ad6-396f-4a4a-9835-e2449341f406`，经 `POST /api/threads/{tid}/runs/stream`
发消息触发（auto 模式、`config.recursion_limit` 需调高，见问题清单 D2）。

| 环节 | 证据 |
|---|---|
| skill 路由 | 消息进入 lead agent 后按 description 命中 `drama-production`（沙箱内开始 MGW 契约操作） |
| per-user 凭证注入 | `media_gateway_credential_middleware` 注入 `MEDIA_GATEWAY_BASE_URL/TOKEN`（沙箱 `env` 可见；token 值不落日志）；`media_gateway_users` 映射存在：deerflow `d99559b6…` → mgw `u_644a5b0f` / `admin-dream.com`（fernet 加密列） |
| 网关侧 owner | 所有 drama 写操作以 `admin-dream.com` 身份落库（`POST /v1/drama` 201，owner 字段核验） |
| 结构落库 | `dr_73d283bb` → `ch_35fd146f`(idx) → `sl_dd31e59e`(name+order) → `pl_48d469a9` → `cl_4393892c`(plot_id+order+budget_tier=10) → 2 shots（clip_id+order+duration_s=5.0，dialogue 带 speaker）全部 201 |
| 资产/托盘 | 场景 `shenzhen_bay_sunrise` 建；`POST …/assets/scenes/…/images`（multipart `files`）201×2；`POST /v1/media` 注册 → media_id；`PATCH clips/…` 全量回传 `ref_tray` 200 |
| 零 GPU 设计门 | `GET …/prompt_preview` → `valid=true`、checks 全过、`token_manifest_match=true`，一次通过 |
| 生成 | `POST …/generate`（无 body）→ **202**，job `img_96b448571d5b`；轮询 `GET …/generate`（单笔租约）→ 完成后租约 404 清除 |
| QC | `GET …/qc` → `qc_ml`，identity/ball/seam/motion/space 全 1.0，`must_fix_storyboard=false`；唯一 M 级 issue（段级 `duration_s=None` 元数据回填缺口）被正确归因为机械层记账、非 citation/prompt/seed，判通过不重生成 —— **QC 归因闭环逻辑在真实网关上行为正确** |
| 断点续作 | 中途 3 次 run 中断（recursion/loop 上限），`stage1_persist.py` + `drama_state.json` 游标重入均正确 SKIP 已落库层，零重复生成（E2E-5b 的恢复语义顺带验证） |
| 产物交付 | 修复代理路由后（见问题清单 D1）：`GET /api/media-gateway/file/v1/drama/dr_73d283bb/chapters/ch_35fd146f/clips/cl_4393892c/media` 带 dev 会话 cookie → **200 video/mp4 5.27MB**；参考图 `/api/media-gateway/file/v1/media/med_…/file` → 200 jpeg；未登录 → 401 |

产物链路最终交付 URL（浏览器 `<video>` 可直接用）：
`/api/media-gateway/file/v1/drama/dr_73d283bb/chapters/ch_35fd146f/clips/cl_4393892c/media`

## 三、问题清单

### 已修复（本仓库，随本次提交）

- **D1 产物代理路由契约偏差（bug）**：`GET /api/media-gateway/file/{path}` 原来
  上游拼成 MGW `/file/{path}`，而真实网关产物路径是 `/v1/media/{id}/file`、
  `/v1/images/jobs/{id}/file`、`/v1/drama/.../media` —— **所有 drama/媒体代理
  产物 404**，且 query string 被丢弃（`?kind=video&variant=0` 必需）。修复：
  按真实契约**原样转发** MGW 路径 + query，旧 `/file/` 前缀保留为 404 回退
  （`backend/app/gateway/routers/media_gateway.py` + 路由测试 12 绿）。
- **D2 skill 文档缺必填字段速查**：SKILL.md 未列落库链各端点必填字段，沙箱
  agent 被迫 400 试错（chapters 缺 idx、storylines 缺 name/order、assets kind
  单复数 404），烧掉大量步数并触发 loop 停机。已补「必填字段速查」表 +
  `assets/{kind}` 复数（`characters|scenes|props`）说明 + generate 无 body 契约。
- **D3 recursion_limit 缺省 100 对该 skill 不够**：创作智能体单 run 工具步
  数远超 100（正式跑建议 `config.recursion_limit>=400` 且轮询用单 bash 循环）。
  属使用约束，已记入本文与 SKILL 提示（文档层面，不改代码）。

### 待办 / 网关侧（不在本仓库修）

- **G1 h3-i2v（fl2va）全档不可用**：MGW 组包与 ComfyUI 自定义节点不匹配
  （`audio_vae` 参数），i2v 提交即 `engine_error`。drama 链路不受影响（走
  ref2va），但 provider 生视频的 i2v 档在真实网关当前不可下单。**网关侧修**。
  ✅ **已修**（minimax-h3-gateway `b8140e8`，2026-11-02）：fl2va 的
  `MiniMaxH3ImageToVideo` 节点无 `audio_vae` 槽，组包已按档位拆分（音频链仅
  ref2va）；实测 h3-i2i-std 202 → completed（~10 min，合法 mp4）。
- **G2 t2va 被管理员禁用**（本机引擎 :30020 不健康）：文生视频只能走
  wan/ltx 系（eta 300–900s）。部署事实，非 bug；skill 已按可用档引导。
  **【2026-11-02 决策：不修复、不测试 h3 t2va，保持禁用】**——用户确认该
  引擎不需要启用；t2va 族会话内仍以抽帧（生图档）形式可用。
- **G3 ref2va 时长/进度**：`eta_s` 低估 5–12 倍；99% 长停顿无阶段反馈。
  建议网关把编码阶段计入 progress（对应 R8-② 生成中实时预览）。
- **G4 段级 `duration_s=None`**：QC 机械层元数据回填缺口（本次 QC 靠归因
  正确跳过；若网关修掉，QC 可全绿）。
- **G5 admin 跨 owner 读不一致**：`GET /v1/drama/{id}` admin 可跨查，
  但 `GET …/clips/{cl}/qc` 以 admin 令牌返回 404 `drama not found`——
  drama.md 说「admin 跨查放行」，子资源未放行。网关侧统一。
- **G6 重出幂等键不去重版本（网关侧，补验实测）**：archive 后 `POST …/generate`
  **不递增 clip.version**（version 只在 recover +1，drama service.py 仅 recover 处
  赋值），幂等键 `drama-<clip_id>-v<version>` 不变 → 底层 /v1/images 同键同体
  **重放旧已完成作业**（不重生成），并把租约重建在该作业上；poller 因
  `_done_jobs` 跳过 → clip state **永久卡 `generating`**。若 fix 后请求体真的变了，
  同键异体则是 409 `conflict`。即：文档承诺的「archive → generate 重出（version+1
  自然换键）」实际不成立，QC 闭环的"修正后重新生成"在真实网关当前走不通，
  只能绕 `recover`（version+1 + 写回元数据）或手工 gwjob 通路。另：
  `generate/cancel` 文档写无 body，实测必须带 JSON body（`{}`）否则 400
  `body: Field required`。**网关侧修**（deer-flow 的 `DramaQCClosedLoop` 按文档
  契约实现，无本仓库改动空间）。
- **G7 drama QC 参考相似度盲区（网关侧设计边界）**：drama QC 的 `identity`
  分测产物内部一致性，不测「产物 vs 托盘参考图」——坏参考（80×80 占位图标）
  生成的完全陌生面孔照样 identity=1.0 / qc_pass。参考相似度评估只在
  `GET /v1/images/jobs/{id}/assessment`（seedmatch-serve 相似度实测），且生图档
  作业（qwen-image）**无保留片段 → assessment 404**（`no kept clip`）。建议：
  drama QC 机械/语义层接 assessment 的 `similarity.identity` 作为参考一致性维度，
  并为生图档保留可评估产物。**网关侧**。

## 四、验收对照（acceptance.md A2b）

- [x] 剧本结构（剧→章→故事线→情节→片段→镜头）全部经 MGW API 落库，deer-flow 无领域表；
- [x] 每镜五要素齐备（动作/表情氛围/dialogue+speaker/托盘引用+camera/size/scene_id/duration_s），设计层校验 `prompt_preview valid=true` 后才进 GPU；
- [x] 资产托盘登记 + `<Picture N>` cite 生效（preview token_manifest_match=true）；
- [x] per-user `h3t_` 令牌全程（owner=admin-dream.com；admin 令牌查子资源 404 佐证隔离）；
- [x] 中断续作（跨 run SKIP 游标恢复，未重建未重复生成）；
- [x] auto 模式完整交付 2 镜小剧（checkpoint 模式未在本轮覆盖）；
- [x] QC「失败→修正→通过」案例（2026-11-02 补验，见下 §五：真实网关 qc_ml FAIL 检出 → 归因 → 归档 → 修正 → 恢复登记 → **qc_pass** 终态闭环；预算控制（≤3 轮 / needs_human 超限）由 mock 层 33 个单测锁定全绿）；
- [x] 角色一致性跨镜角色卡（2026-11-02 补验，见下 §五：2 clip 共引同一角色卡 media_id，prompt_preview 双向 token_manifest_match=true，两镜产物 VLM 目检一致性通过）；
- [ ] 分镜板 t2g 目检（本轮直接场景参考图目检替代）。

## 五、A2b 补验（2026-11-02 第二轮）

> 账号：MGW users 模式新建 `a2bverify`（admin 代建）；作业预算执行：生图 4 笔 +
> 生视频 1 笔（h3-ref-std，≤2 约束内），总作业 5 ≤ 6。

### 5.1 QC「失败→修正→通过」（真实网关 + mock 层）

构造：drama `dr_4a1b9bd0`（1 clip 5s，《黄昏街头》），**故意用坏角色参考**
（80×80「无图像」占位图标 ref4.jpg 上传为角色定妆照并注册 media，ref_tray 单条引用）。

| 步骤 | 结果 |
|---|---|
| 设计门 | `prompt_preview valid=true`（坏参考在数据层完全合法——设计层不校验图像内容） |
| `POST …/generate` | 202（h3-ref-std，约 12 min completed） |
| FAIL 检出 | `GET …/qc` → **`qc_ml`**：scores 全 1.0，唯一 issue `M 机械层不通过：时长非正（duration_s=None）`（即 G4 元数据回填缺口——真实网关上可稳定复现的 FAIL 终态） |
| 归因（`qc.attribute_qc` 跑真实 QC body） | 未命中 citation/seed 特征 → **prompt 兜底**，reasons 原文记录 |
| 修正策略（`qc.remediation_plan(attempt=1, max=3)`） | `revise_prompt`（改五要素、先归档再重出） |
| 归档 | `POST …/archive {version:1}` 200（审计 `clip.archive`，解锁重出） |
| fix_fn | `PATCH shots {duration_s:5}` 200（修正元数据） |
| 重出 | `POST …/generate` 202 —— **但见问题 G6**：version 未自增，幂等键同键，网关把租约重新指向**旧已完成作业**，poller 因 `_done_jobs` 跳过 → state 卡 `generating`（`cancel` 需带 body `{}` 才接受） |
| 恢复登记（绕开 G6 的合法通路） | `POST …/recover {engine_media_path, duration_s:5.0}` 202 → **version 2、state=qc_pending**（recover 写 clip.duration_s，正是修正内容的落库点） |
| PASS 终态 | `POST …/qc {force:true}` → **`qc_pass`，scores 全 1.0，issues 空**——「失败检出 → 归因 → 修正 → 重登记 → 通过」闭环在真实网关成立 |
| 产物交付 | `GET …/clips/{cl}/media`（owner 令牌）200 video/mp4 1.5MB |

**预算控制**（重试上限 / 超时）：mock 层锁定——`tests/test_media_gateway_qc.py`
33 passed（dev 容器内跑），含 `失败→归因→修正→通过` 全环、reseed 亦归档、
`max_iterations` 耗尽 → `needs_human`（history 逐轮 QC+归因可追溯）、超时
`task_timeout`（poll 上限耗尽抛错）。真实网关侧单轮 FAIL 的归因/归档/重出契约
与 mock 一致（除 G6）。

**QC 失败注入的现实边界**：坏参考图**不会**触发 identity 低分——真实网关 drama
QC 的 `identity` 维度测的是产物内部/时序一致性，**不测「产物 vs 参考图」相似度**
（VLM 对坏参考生成的陌生面孔照样打 1.0）。drama QC 的 FAIL 只能从机械层
（G4 类）或 must_fix_storyboard 触发；参考相似度评估在 `/v1/images/jobs/{id}/assessment`
（seedmatch-serve），且**生图档作业无保留片段，assessment 404**——QC 环节的
参考相似度盲区见问题 G7。

### 5.2 跨镜角色卡一致性（纯生图档）

构造：drama `dr_0e66f1c3`（《双镜》），1 章 1 情节 **2 clip**（各 1 镜 5s：
黄昏天台 / 夜晚便利店），角色卡 `林晚儿`（subject_id=7）。

| 环节 | 结果 |
|---|---|
| 角色卡生成 | qwen-image-std 文生图定妆照（1024×1344；特征：黑发+银簪左位+右眼下痣+青色鹤纹汉服） |
| 资产落库 | `POST …/characters {name,subject_id}` 201；定妆照经 `POST …/assets/characters/{name}/images`（multipart）201 + `POST /v1/media` 注册 → `med_b1dc43ced93d` |
| 跨镜引用 | 两个 clip 均 `PATCH ref_tray=[{media_id: med_b1dc43ced93d, kind: character}]` 200；**数据层同一 media_id 双 clip 共引**成立 |
| 设计门 | 两 clip `prompt_preview` 均 `valid=true`、`pictures[0]` 同源 `tray[0]`、`token_manifest_match=true`；两镜正文 `<Picture 1>` 引用齐备 |
| 两镜产物 | qwen-image-std + `reference_images=[定妆照]` 各一笔（1792×1024） |
| 像素一致性目检（VLM，see.py） | 卡 vs 镜1：**consistent**（发/簪位/眼色/痣/服五特征全对齐，仅背景光照不同）；镜1 vs 镜2：发型/银簪位置/鹤纹位置/痣位/眼色逐项一致，**跨镜一致性通过**（主观报告，非强制达标项） |

注：qc.attribute_qc 的 assessment 叠加通路（R2-3）在本机无法用生图档验证
（assessment 依赖保留片段，见 G7），其归因逻辑由 mock 单测覆盖。

**结论：drama-production R4 联调通过（有条件）**——条件为网关侧 G1（i2v）
不影响 drama 主链路，代理路由修复已落地；「失败→修正→通过」与角色卡一致性
两项已于 2026-11-02 第二轮补验通过（§五），唯「修正后同键重生成」受网关侧
G6 阻断（以 recover 通路闭环）。

**补验结论（2026-11-02）**：A2b 两项遗留均闭环——QC 失败→修正→通过在真实
网关走通（终态 qc_pass），角色卡跨镜引用（数据层）与像素一致性（目检）通过。
新增网关侧待办 G6（重出幂等键版本不递增）、G7（QC 参考相似度盲区）；
deer-flow 侧无代码改动。
