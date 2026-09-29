# 长江雨课堂定时签到+听课答题
**🌟 雨课堂、荷花、黄河等应该就HOST和API不同吧，可以自己试试，改下API应该就行?**

## 方法1 Github Actions
### 🌟 说明
工作流每 5 分钟尝试启动一次。若仓库已有 `data/course_schedule.json`，就按其中的实际日期和时间、以及 `FILTERED_COURSES` 判断是否处于监听时段（北京时间）；时段外跳过依赖安装和脚本。时段内持续运行，每约 5 分钟检查一次课程，发现课程后签到并按 `FILTERED_COURSES` 配置进入监听。到达该时段结束时间后，本轮停止查课并关闭监听连接。同一轮运行不会重复处理同一课堂。首次尚未生成课表文件时，仍使用旧的 `LISTEN_WINDOWS` 时间窗。

独立的 `Refresh HFUT course schedule` 工作流每天北京时间约 02:00 尝试运行，但同一学期课表已保存且课程名单未变时会直接跳过登录。新学期或名单变化时，自动从教务系统获取课表并提交精简文件。公开文件仅含学期编号、所选课程名称、每次课程的日期与起止时间；不会保存学号、密码、Cookie、教室、教师或原始响应。课程临时调整不会自动更新本学期已保存的课表。

签到记录仍会写到运行环境的 `log.json`，但该文件不再提交到公开仓库。GitHub Actions 运行环境是临时的，任务结束后不会在仓库中保留这些记录；本地运行时可在本机查看。旧提交中的历史记录不会因此消失。

**⚠️ GitHub Actions 的定时触发可能延迟或漏跑，无法保证在指定时刻准时启动，也无法保证严格每 5 分钟检查一次。若本轮错过整个时段，不会补跑。**

**⚠️ 工作流使用并发限制：同一时间最多运行一轮，等待中的定时任务可能被更新的任务替换，不能依赖排队任务接替异常退出的监听。**

解决建议：

1.通过Github Actions API搭配自动化任务，定几个重要的上课时间节点，发送网络请求运行Action 

2.若需要更可控的调度，可转到第二种使用方法，在自己的服务器上部署；仍需自行监控服务和网络状态

**⚠️ 安装依赖较大，约需要75秒后正式开始运行**

**⚠️ 注意 若Cookie过期，Github会发邮件提示运行失败**

### 🚀 开始配置
1.按下面教程拿到SESSIONID，或者自己抓APP的包

2.按图中路径，配置名为SESSION的环境变量，值为SESSIONID的值
![图片1](src/img/Step_1.png)
![图片2](src/img/Step_2.png)

3.再配置两个secret，AI_KEY和ENNCY_KEY，用于搜题答题，获取方式在末尾

4.再配置一个 secret `FILTERED_COURSES`，用英文逗号隔开，填写需要监听的课程完整名称。使用课表自动获取功能时不得留空，以免将全部课程公开；名称必须与教务课表一致。

例如：计算机组成原理,数据结构

5.配置教务系统登录用的 `HFUT_USERNAME`（学号）和 `HFUT_PASSWORD`（信息门户密码）两个 Secrets。它们只用于课表更新工作流，不会输出或写入课表文件。首次配置后，可在 Actions 页面手动运行一次 `Refresh HFUT course schedule` 来提前生成课表；即使不手动运行，定时工作流也会自动尝试。若教务系统要求图片验证码，工作流会在同一登录会话中用英文 Tesseract OCR 识别，最多提交两次登录尝试；不会保存或打印验证码图片与识别结果。OCR 并不保证成功，失败后保留已有课表并退出，避免无限尝试。校内网络/VPN 限制或登录接口变更也可能导致失败，请查看该工作流的简短错误原因。

6.可保留原先名为 `LISTEN_WINDOWS` 的 Secret，作为首次课表获取成功之前的临时后备时间窗。例如：

```text
MON=08:00-10:00,14:00-16:00
TUE=09:30-11:30
WED=08:00-09:45
```

使用 `MON` 到 `SUN` 表示周一到周日，时间均为北京时间、24 小时制，格式必须是 `HH:MM-HH:MM`。每天可填多个时段，用英文逗号分隔；不同日期换行，也可用英文分号分隔。每段最长 5 小时，同一天的时段不能重叠，也不能跨午夜；跨午夜请拆成前后两天分别填写。课表文件存在后以课表为准，不再使用 `LISTEN_WINDOWS`；即使文件属于旧学期，也不会退回每周固定时间窗。修改 Secret 后仅影响后续运行，已启动的一轮不会自动更换时段。

7.去 Actions 页面手动点击 `Run workflow` 可检查运行结果。手动触发即使在配置时段外也会单次查课；若在时段内触发，则运行到该时段结束。
![图片4](src/img/Step_4.png)

### Android 课前提醒（ICSx⁵）

课表更新工作流还会自动生成公开的 [`data/course_schedule.ics`](data/course_schedule.ics)，每次课程都带有课前 10 分钟提醒。它只包含已筛选课程的名称和时间，不包含学号、密码或教务会话。手机同步后，提醒由 Android 本地日历发出，不依赖 GitHub Actions 在上课时准时运行。

在 Android 手机上安装 ICSx⁵，并添加以下只读网络日历地址（设置一次即可）：

```text
https://raw.githubusercontent.com/frewily/Fuck-Yangtze-RainClassroom/main/data/course_schedule.ics
```

同步后，在手机日历 App 中开启该日历的显示与通知权限，检查事件是否有“提前 10 分钟”提醒；部分日历 App 会忽略订阅文件中的提醒，需要在 App 内为该日历设置默认提醒。允许 ICSx⁵ 后台同步，并关闭针对它和日历 App 的严格省电限制。首次订阅或新学期更新可能需要等待同步，建议先核对一节近期课程的时间和提醒。即使 GitHub Actions 后续漏跑，已同步到手机的课程仍会按本地时间提醒。

## 方法2 部署在服务器
### 🌟 说明

**⚠️ 注意 注意设置好运行自动化时的Cookie过期的提醒**

### 🚀 开始配置
1.进入config.py，修改isLocal变量为True

2.填写config.ini

3.安装依赖
```bash
pip install -r requirements.txt
```

4.配置config.py中
```python
filtered_courses=[
        # 默认为空 所有课题监听课程测试
        # 若填写课程名称 则只监听列表里的课，其余课仅签到,建议按自己需求添加
        "计算机组成原理","数据结构"
]
```

5.定时运行start.py(推荐使用宝塔面板定时任务，具体教程自行搜索)
```bash
python start.py
```


## 获取SESSIONID方式

访问 https://changjiang.yuketang.cn/ ,登录后，按F12
![图片1](src/screenShot/1.png)
![图片2](src/screenShot/2.png)
![图片3](src/screenShot/3.png)
![图片4](src/screenShot/4.png)

复制粘贴得到的id到config.txt，并保存即可

## [获取AI_KEY(AI 用于解题或辅助题库搜题规格化答案)](https://api.chatanywhere.org/v1/oauth/free/render)
## [获取ENNCY_KEY(言溪题库 用于题目为空时搜题)](https://tk.enncy.cn/)
