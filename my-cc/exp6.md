# 实验六 · 基于hyper-v与DoraCloud的云桌面部署

## 实验目的

1.  掌握桌面云的基本原理

2.  掌握windows server创建桌面云

## 实验原理

Hyper-V是Microsoft的硬件虚拟化产品。 它用于创建并运行计算机的软件版本，称为"虚拟机"。 每个虚拟机都像一台完整的计算机一样运行操作系统和程序。 如果需要计算资源，虚拟机可提供更大的灵活性、帮助节省时间和金钱，并且与在物理硬件上运行一个操作系统相比，虚拟机可以更高效地使用硬件。

DoraCloud是一款多平台的桌面虚拟化管理软件，支持Hyper-V、VMware、Proxmox、XenServer等多种虚拟化平台。DoraCloud在虚拟化平台上具有极大的灵活性，允许您的组织自由选择合适的IT基础设施来构建桌面云；也允许您的组织重用现有的IT设施基础，而不是抛弃现有的IT基础设施。

DoraCloud安装程序在Windows Server上安装了一个 DeskpoolService服务程序，并在Hyper-V虚拟化平台上创建了DoraCloud 管理VM。 DoraCloud通过DeskpoolService对Hyper-V进行控制，负责创建桌面虚拟机，实现对桌面虚拟机的管理。

![](img/image155.png)

https://docs.doracloud.cn/getting-started/quickstart/

## 实验仪器材料

1.  个人计算机一台

2.  vmware worksation

3.  windows server 2019

4.  DoraCloud_Hyper-V_2012R2_V4

5.  DoraClient

## 实验步骤

### 环境规划

| **类别** | **操作系统版本** | **数量** | **硬件配置**           |
|:---------|:-----------------|:---------|:-----------------------|
| 服务器   | WinServer2019    | 1        | 4vCPU/10G内存/200G硬盘 |
| 桌面终端 | Win7             | 2        | 1vCPU/2G内存/50G硬盘   |

注：IP根据自己实际的实验环境修改，下同。

### 安装windows server 2019

在Vmware Workstation中安装windows server 2019，

![](img/image156.png)

![](img/image157.png)

Windows产品密钥输入：WMDGN-G9PQG-XVVXX-R3X43-63DFG

![](img/image158.png)

后续按常规默认配置安装即可。

![](img/image159.png)

系统安装完后，会自动安装vmware tools，根据提示安装即可。

**安装完成后关机，并更改虚拟机设置，启动虚拟化引擎。**

![](img/image160.png)

系统重启后，在服务器管理器菜单栏点击"管理"-"添加角色和功能"。

![](img/image161.png)

![](img/image162.png)

![](img/image163.png)

![](img/image164.png)

勾选Hyper-v选项。

![](img/image165.png)

虚拟交换机勾选网卡。

![](img/image166.png)

其余按默认配置安装即可。

在安装完成后重启系统。

![](img/image167.png)

![](img/image168.png)

### 安装DoraCloud

#### 创建、编辑模板

在windows server 2019虚拟机的C盘创建文件夹soft，然后把

DoraCloud_Hyper-V_2012R2_V4_0_0 .msi文件拖到soft文件夹。

![](img/image169.png)

双击DoraCloud_Hyper-V_2012R2_V4_0_0 .msi进行安装，所有选项默认即可。安装完成后会生成一个centos的操作系统登录界面，在登录界面中可以查看doraCloud管理地址：

![](img/image170.png)

在宿主机浏览器中输入管理地址即可访问后台。

![](img/image171.png)

在登录界面右下角点击"下载DoraCloud客户端"下载客户端程序，并安装。

然后在登录界面输入默认管理账号为admin，密码为DoraCloud（注意大小写）登录。

登录后初始化向导一路点击下一步

![](img/image172.png)

![](img/image173.png)

![](img/image174.png)

![](img/image175.png)

为doraCloud管理地址配置固定IP，防止IP重启后变化。

![](img/image176.png)

![](img/image177.png)

确定后点完成，需要等待一两分钟后重新输入登录地址重新登录一次。

登录后关闭向导，并把localImages.zip拖到windows server 2019虚拟机内，解压缩到C:\DoraCloud，如下图所示：

![](img/image178.png)

在管理平台上点击"模板"-"本地镜像仓库"

![](img/image179.png)

![](img/image180.png)

如果看不到win7x86base镜像，点击一下页面的刷新按钮（页面上的刷新，不是浏览器的刷新）

![](img/image181.png)

点击恢复按钮，从仓库中恢复镜像。

恢复时间会比较长，过2-3分钟等事情列表进度到100%即可在模板列表中看到恢复的模板。

模板为纯净的系统，在模板上安装用户必要的软件再创建虚拟机。

点击模板"编辑"按钮，创建临时副本，编辑操作系统：

![](img/image182.png)

通过远程桌面连接到模板虚拟机

![](img/image183.png)

![](img/image184.png)

用户名为:administrator，密码为123456

登录系统后可以安装系统应用必要的软件，一般企业都会根据不同的岗位安装好对应的软件，这里仅安装wps软件为例（实验中可随便安装一个小软件).

![](img/image185.png)

安装好软件后关闭远程桌面。

返回"编辑模板"界面，点击确认

![](img/image186.png)

全部选"是"，再确定。

![](img/image187.png)

确定后，再点击"下一步"

点击"准备模板"，经过2-4分钟左右，等模板创建完成。

![](img/image188.png)

创建完成后，可以先测试模板可用性，然后保存模板。

![](img/image189.png)

测试模板可用后，再点击"保存模板"。

#### 创建桌面池

桌面池是通过同一模板创建一组虚拟机的集合。通过配置桌面池的策略可以实现桌面虚拟机的动态创建和回收。一般会根据不同岗位和场景，配置和软件要求一样的终端设置同一个桌面池。

![](img/image190.png)

点"继续"

输入桌面池名称（可中文），比如定义一个销售组的池，以后销售组的员工均使用此模板下的虚拟机，输入虚拟机名称前缀（字母），后缀（序号），内存和CPU配置等信息。

![](img/image191.png)

点击下一步：

![](img/image192.png)

设置最大创建虚拟机数量和预创建虚拟机数，一般会根据部门员工人数进行设置，预创建虚拟机数即配置后立即创建的虚拟机。这里资源有限，可以先创建2个或者3个。

桌面池类型分为公用桌面池和专用桌面池，针对企业岗位一般选用专用桌面池，每个员工有自己的专属桌面，数据永久保存。针对实验室等场景，一般选用公用桌面池，便于资源回收。

![](img/image193.png)

这里没有集中式存储，直接点击"确定"即可。

![](img/image194.png)

#### 创建用户

创建用户和群组，用于登录虚拟机。

![](img/image195.png)

点击继续，创建用户，并且绑定桌面池。

![](img/image196.png)

提交后，切换到桌面计算机，查看虚拟机是否在运行。

![](img/image197.png)

打开DoraCloud客户端

![](img/image198.png)

![](img/image199.png)

输入DoraCloud服务器管理地址

![](img/image200.png)

输入上面创建的用户密码

![](img/image201.png)

点击登录，即可登录到云桌面。

如果弹出系统登录框，可以输入默认账号administrator,密码123456登录。

![](img/image202.png)

确认后即可登录到系统。

![](img/image203.png)

## 实验结果与分析

实验成功完成基于 Hyper-V 与 DoraCloud 的云桌面部署，搭建了 1 台 Windows Server 2019 服务器和 2 台 Win7 桌面终端，成功创建桌面池与测试用户，DoraClient 客户端可正常登录云桌面，预安装的 WPS 软件能正常运行。关键在于启用 Hyper-V 虚拟化引擎、正确配置 DoraCloud 固定 IP 及模板系统准备，避免了 IP 漂移和桌面创建失败的问题；桌面池的公用 / 专用配置可适配不同场景，验证了部署方案的灵活性。

## 结论与体会

本次实验达成预期目标，掌握了桌面云基本原理及 Windows Server 环境下的部署流程。通过实践感受到虚拟化技术在资源利用和管理上的优势，DoraCloud 对多平台的支持降低了现有 IT 设施的复用成本。同时意识到，IP 配置、模板编辑等细节直接影响部署成败，后续需进一步熟悉桌面池调度和用户权限精细化管理的配置。
