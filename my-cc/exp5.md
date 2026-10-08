# 实验五 · 共享式存储的多节点虚拟系统部署

## 实验目的

1.  掌握esxi的安装与使用

2.  掌握ip-san的原理与应用

3.  掌握使用openfiler模拟ip-san环境

4.  掌握esxi挂载ip-san存储（iscsi)

5.  掌握Vcenter管理ESXI节点(选做)

## 实验原理

ESXi是由VMware开发的一款企业级虚拟化平台，它能够将物理服务器转变为多个虚拟化环境，并允许每个虚拟机运行不同的操作系统。与传统的硬件运行系统不同，ESXi提供了一个高效、可靠的虚拟化平台，使得多个操作系统可以在同一台物理机器上并行运行。该平台主要用于企业级数据中心，提供高效的资源分配和管理能力。

ESXi支持挂载本地磁盘、NAS、IP-SAN、FC-SAN等多种构架的存储。如果需要实现虚拟机自动迁移的功能，需要多节点连接共享式存储。本实验通过openfiler启动ISCSI存储模拟一台IP-SAN存储设备，两台ESXi共享存储的方式模拟搭建一个多节点云计算环境。

![](img/image107.png)

## 实验仪器材料

1.  个人计算机一台

2.  vmware worksation

3.  ESXI 6.7

4.  openfiler

5.  vcenter6.7

## 实验内容与步骤

### 环境规划

| **操作系统版本** | **硬件配置** | **IP地址规划** | **用户名/密码** |
| --- | --- | --- | --- |
| Openfiler | 2vCPU/2G内存/10G+50G硬盘 | IP:192.168.128.101/24 Gw:192.168.128.2 DNS:192.168.128.2 | root/123456 |
| ESXI-1 | 2vCPU/12G内存/50G硬盘 | IP:192.168.128.102/24 Gw:192.168.128.2 DNS:192.168.128.2 | root/gccEsxi1. |
| ESXI-2 | 2vCPU/12G内存/50G硬盘 | IP:192.168.128.103/24 Gw:192.168.128.2 DNS:192.168.128.2 | root/gccEsxi1. |

注：IP根据自己实际的实验环境修改，下同。

#### 安装与配置openfiler

‌[Openfiler](https://www.baidu.com/s?rsv_dl=re_dqa_generate&sa=re_dqa_generate&wd=Openfiler&rsv_pq=b02a636c02074d4f&oq=openfiler%E4%BB%8B%E7%BB%8D&rsv_t=b2e05VWwSw6CK92sU0CoEkNiGTJW85VWBU7tC4AtVxdlEdG9gx5i7QAaWlwTKObgNEX3zLd+6Hx/&tn=57095150_10_oem_dg&ie=utf-8)‌是一个基于[Linux](https://www.baidu.com/s?rsv_dl=re_dqa_generate&sa=re_dqa_generate&wd=Linux&rsv_pq=b02a636c02074d4f&oq=openfiler%E4%BB%8B%E7%BB%8D&rsv_t=b2e05VWwSw6CK92sU0CoEkNiGTJW85VWBU7tC4AtVxdlEdG9gx5i7QAaWlwTKObgNEX3zLd+6Hx/&tn=57095150_10_oem_dg&ie=utf-8)的开源网络存储管理操作系统，通过Web界面进行管理，支持多种网络存储协议，如[iSCSI](https://www.baidu.com/s?rsv_dl=re_dqa_generate&sa=re_dqa_generate&wd=iSCSI&rsv_pq=b02a636c02074d4f&oq=openfiler%E4%BB%8B%E7%BB%8D&rsv_t=b2e05VWwSw6CK92sU0CoEkNiGTJW85VWBU7tC4AtVxdlEdG9gx5i7QAaWlwTKObgNEX3zLd+6Hx/&tn=57095150_10_oem_dg&ie=utf-8)、[NFS](https://www.baidu.com/s?rsv_dl=re_dqa_generate&sa=re_dqa_generate&wd=NFS&rsv_pq=b02a636c02074d4f&oq=openfiler%E4%BB%8B%E7%BB%8D&rsv_t=b2e05VWwSw6CK92sU0CoEkNiGTJW85VWBU7tC4AtVxdlEdG9gx5i7QAaWlwTKObgNEX3zLd+6Hx/&tn=57095150_10_oem_dg&ie=utf-8)等，能够在单一框架中提供基于文件的网络连接存储（NAS）和基于块的存储区域网（SAN）。‌

1.  在vmware workstation中创建虚拟机，并在光驱中加载openfileresa-2.99.1-x86_64-disc1.iso。

![](img/image108.png)

![](img/image109.png)![](img/image110.png)![](img/image111.png)

![](img/image112.png)

![](img/image113.png)

![](img/image114.png)![](img/image115.png)

2.  配置完成后开机，在启动画面按回车开始安装。

![](img/image116.png)

点击next，在弹出的对话框中点击yes。到网络设置的页面手动输入主机名、IP、网关、DNS等信息（IP、网关需要根据自己的实际环境修改）。

![](img/image117.png)

3.  一直点next，在密码设置页面输入ROOT密码，即可进行安装。安装完成后点击reboot按钮进行重启。

![](img/image118.png)

看到此界面，打开浏览器输入以上openfiler管理网址（IP根据实际环境更改），输入默认用户名openfiler，默认密码password。

![](img/image119.png)

![](img/image120.png)

进入此WEB界面即安装成功。

4.  编辑虚拟机，为虚拟机增加一个硬盘（50G）

![](img/image121.png)

5.  在openfiler管理界面切换到services面板，开启iscsi target服务。

![](img/image122.png)

切换到Volumes 面板创建卷组。

![](img/image123.png)

![](img/image124.png)

![](img/image125.png)

![](img/image126.png)

![](img/image127.png)

创建iscsi target.

![](img/image128.png)

![](img/image129.png)

添加IP访问许可

![](img/image130.png)

#### 安装与配置ESXI

Vmware workstation创建虚拟机，安装esxi节点1。

![](img/image131.png)

![](img/image132.png)

![](img/image133.png)

![](img/image134.png)

![](img/image135.png)

按回车进入安装

![](img/image136.png)

按F11继续安装

![](img/image137.png)

设置ROOT密码，需要大小写字母、数字和特殊字符三种组合（本实验使用gccEsxi1.）。

![](img/image138.png)

安装完成后重启。

按F2进入管理界面。

![](img/image139.png)

进入管理网络配置界面

![](img/image140.png)

![](img/image141.png)

设置IP地址为静态地址

![](img/image142.png)

按回车，然后按ESC退出，弹出保存提示框，按提示保存即可。

本实验不需要联接互联网，DNS可设可不设。

打开浏览器，输入https://192.168.46.101(IP根据自己实验环境修改)

输入用户名root和安装时设置的root密码登录。

![](img/image143.png)

看到以上界面即安装成功。

以同样的方法安装ESXI-2。（不能以克隆的方式安装，否则vcenter无法纳管）

打开openfiler，复制此前创建的iscsi target地址：

![](img/image144.png)

打开ESXI-1的web管理界面，点击左侧存储，再点击右侧"适配器"打开ISCSI设置界面。

![](img/image145.png)

![](img/image146.png)

勾选"已启用"，并点击"添加静态目标"，输入openfiler上复制的iscsi target 地址，输入openfiler服务器IP。

![](img/image147.png)

正常情况会出现多一个适配器，如没出现，等几十秒后刷新一下页面。

![](img/image148.png)

刷新页面后，左侧菜单会多出iscsi的存储，点击存储图标，再点击右侧"新建数据存储"

![](img/image149.png)

分区名称可以自定义（不要中文）

![](img/image150.png)

点击下一步，确认即可。完成后，左侧点击"存储"菜单，回到存储界面，点击右侧"数据存储"，可以看到多了一个刚才命名的分区。

![](img/image151.png)

点击分区名称进入分区，点击"数据存储浏览器"可以管理里面的文件，可以尝试往分区上传一个文件。

![](img/image152.png)

![](img/image153.png)

打开ESXI-2的WEB管理界面，点击左侧"存储"菜单，右侧切换到"适配器"，点击"软件iscsi"，打开ISCSI配置界面，点击"已启用，再点击添加静态目标，输入openfiler创建的iscsi target地址和openfiler的IP（重复ESXI-1的操作）。

可以看到在ESXI-1上创建的分区，打开"数据存储浏览器",可以看到在ESXI-1上上传的文件。

![](img/image154.png)

到这里，已经创建了一个共享存储的双节点虚拟机系统。可以尝试在任一个节点上新建虚拟机。

接下来需要安装一个VCenter统一纳管ESXI，实现资源迁移和容灾（以下部分选做）。

## 实验结果与分析

实验成功搭建了基于Openfiler模拟IP-SAN的双ESXi节点共享存储系统。Openfiler正常启用iSCSI服务并创建卷组与目标，两台ESXi节点均成功挂载共享存储，实现了文件跨节点共享访问，数据一致性得到保障。通过Web界面可正常管理存储资源与虚拟机，满足多节点虚拟化环境的基础需求。但操作中需注意IP配置一致性与iSCSI目标地址准确性，否则会导致挂载失败。

## 结论与体会

本次实验掌握了ESXi与Openfiler的核心配置流程，理解了IP-SAN共享存储的实现原理。多节点共享存储的部署让我体会到虚拟化环境中资源整合的优势，也认识到网络配置、存储权限等细节对实验成败的关键影响。后续可进一步探索VCenter的纳管功能，深化对虚拟化资源调度与容灾机制的理解。
