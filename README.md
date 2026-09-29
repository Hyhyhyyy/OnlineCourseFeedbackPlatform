<p align="center"><img src="brand/readme-banner.png" alt="课镜" width="100%"></p>

# 课镜 · 教学复盘研究工作台

课镜面向有字幕网课，将原始教学快照、字幕、教学片段和人工复核记录联系起来，服务于多模态数据集构建、弹幕整体状态评估和片段级教学分析。

## 当前基本原型

- 课程来源与获准使用记录；本地视频、字幕、原始截图导入。
- B站视频与弹幕获取，B站中文字幕接口适配及SRT/VTT/JSON检查。
- [原生播放器采集扩展](integrations/bilibili-capture)：按时间定位，等待中文字幕稳定，截取实际显示的画面与弹幕并回传本机。真实B站页面仍待联调。
- 字幕候选边界、片段编辑、快照按实际时间关联及原图回看。
- 快照三段式报告、片段两项报告的固定结构；人工填写、修订来源保护、JSON/HTML导出。
- 模型连接与运行方式：通用大模型 API、本地模型、校园服务器；保留服务商配置、模型列表、文本与图文连接验证及用量查询，运行方式随研究任务保存。

状态分类、快照模型和研究分段方法等待方案确定后接入。本轮保留明确的待接入状态；已有人工记录可以独立管理。原课程发布与学生互动业务已退出应用入口。

## 启动

```powershell
.\.venv\Scripts\python.exe src/data_server.py --port 8766
```

也可运行 `start-workbench.ps1`。打开 [本地工作台](http://127.0.0.1:8766/)。本轮基本功能无需启动大模型。

侧栏“模型连接与运行方式”管理连接。`start-local-demo.ps1` 启动已有本地模型与工作台；校园服务通过 `COURSE_CAMPUS_BASE_URL`、`COURSE_CAMPUS_MODEL`、可选 `COURSE_CAMPUS_KEY` 环境变量配置，保留待真实接入测试状态。连接验证与研究分析接口接入分别记录。

新环境：Python 3.10以上，创建虚拟环境后安装 `requirements-media.txt`。网页采集扩展需由使用者在Chrome或Edge加载，步骤见[开发说明](docs/research-prototype.md)。

## 结构

- [开发说明 V1.0](docs/research-prototype.md)：当前范围、使用步骤、接口、证据记录与验证边界。
- [研究服务](src/research_server.py)：本地接口、素材获取、采集配对。
- [数据与报告](src/research_workbench.py)：字幕、片段、原图、报告与修订。
- [工作台页面](web/research.html)：四个工作区。
- [原生采集扩展](integrations/bilibili-capture)：真实播放器截图。

## 验证

70项Python测试通过，新增JavaScript通过语法检查；合成视频已完成浏览器中的导入、字幕检查、快照上传和报告框架建立。真实B站登录字幕接口与原生采集扩展需在用户浏览器中联调，尚未报告真实采集成功率或模型效果。

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

运行数据保存在 `outputs/research` 并由Git忽略。原始材料、历史试验与未提交的研究文档修改保留供追溯。
