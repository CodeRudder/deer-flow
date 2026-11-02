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
- **G2 t2va 被管理员禁用**（本机引擎 :30020 不健康）：文生视频只能走
  wan/ltx 系（eta 300–900s）。部署事实，非 bug；skill 已按可用档引导。
- **G3 ref2va 时长/进度**：`eta_s` 低估 5–12 倍；99% 长停顿无阶段反馈。
  建议网关把编码阶段计入 progress（对应 R8-② 生成中实时预览）。
- **G4 段级 `duration_s=None`**：QC 机械层元数据回填缺口（本次 QC 靠归因
  正确跳过；若网关修掉，QC 可全绿）。
- **G5 admin 跨 owner 读不一致**：`GET /v1/drama/{id}` admin 可跨查，
  但 `GET …/clips/{cl}/qc` 以 admin 令牌返回 404 `drama not found`——
  drama.md 说「admin 跨查放行」，子资源未放行。网关侧统一。

## 四、验收对照（acceptance.md A2b）

- [x] 剧本结构（剧→章→故事线→情节→片段→镜头）全部经 MGW API 落库，deer-flow 无领域表；
- [x] 每镜五要素齐备（动作/表情氛围/dialogue+speaker/托盘引用+camera/size/scene_id/duration_s），设计层校验 `prompt_preview valid=true` 后才进 GPU；
- [x] 资产托盘登记 + `<Picture N>` cite 生效（preview token_manifest_match=true）；
- [x] per-user `h3t_` 令牌全程（owner=admin-dream.com；admin 令牌查子资源 404 佐证隔离）；
- [x] 中断续作（跨 run SKIP 游标恢复，未重建未重复生成）；
- [x] auto 模式完整交付 2 镜小剧（checkpoint 模式未在本轮覆盖）；
- [ ] QC「失败→修正→通过」案例（本轮 QC 一次通过；归因/跳过路径已验证，修正重生成路径待后续补）；
- [ ] 角色一致性跨镜角色卡（本轮无人物角色，纯场景剧；待有人物样本补验）；
- [ ] 分镜板 t2g 目检（本轮直接场景参考图目检替代）。

**结论：drama-production R4 联调通过（有条件）**——条件为网关侧 G1（i2v）
不影响 drama 主链路，代理路由修复已落地；「失败→修正→通过」与角色卡一致性
两项留待补充验证（不阻塞 drama 上线判定）。
