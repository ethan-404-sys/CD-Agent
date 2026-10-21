# exp8 第 0 步：本地数据与权重盘点

只读取目录、图像头、配置和日志；尺寸按全部图像头统计，不解码整幅像素。

| 数据集 | 路径 | 划分 | t1/t2/GT | 完整配对 | 尺寸（宽×高） |
| --- | --- | --- | --- | ---: | --- |
| sysu | /guisongxia01/pangchao/wangyihan/datasets/lim1/SYSU-CD | test | 4000/4000/4000 | 4000 | t1: 256x256 (4000); t2: 256x256 (4000); gt: 256x256 (4000) |
| sysu | /guisongxia01/pangchao/wangyihan/datasets/lim1/SYSU-CD | train | 16000/16000/16000 | 16000 | t1: 256x256 (16000); t2: 256x256 (16000); gt: 256x256 (16000) |
| levir | /guisongxia01/pangchao/wangyihan/datasets/LEVIR-CD/test_256 | test | 2048/2048/0 | 0 | t1: 256x256 (2048); t2: 256x256 (2048); gt:  |
| levir | /guisongxia01/pangchao/wangyihan/datasets/LEVIR-CD/test_original | test | 128/128/128 | 128 | t1: 1024x1024 (128); t2: 1024x1024 (128); gt: 1024x1024 (128) |
| levir | /guisongxia01/pangchao/wangyihan/datasets/LEVIR-CD/train_original | train | 445/445/445 | 445 | t1: 1024x1024 (445); t2: 1024x1024 (445); gt: 1024x1024 (445) |
| levir | /guisongxia01/pangchao/wangyihan/datasets/LEVIR-CD/val_original | val | 64/64/64 | 64 | t1: 1024x1024 (64); t2: 1024x1024 (64); gt: 1024x1024 (64) |
| whu | /guisongxia01/pangchao/wangyihan/datasets/lim1/WHU-CD | test | 690/690/690 | 690 | t1: 512x512 (690); t2: 512x512 (690); gt: 512x512 (690) |
| whu | /guisongxia01/pangchao/wangyihan/datasets/lim1/WHU-CD | train | 1260/1260/1260 | 1260 | t1: 512x512 (1260); t2: 512x512 (1260); gt: 512x512 (1260) |
| whu | /guisongxia01/pangchao/wangyihan/datasets/lim1/WHU-CD/test | test | 690/690/690 | 690 | t1: 512x512 (690); t2: 512x512 (690); gt: 512x512 (690) |
| second | /guisongxia01/pangchao/wangyihan/datasets/lim1/SECOND | test | 1694/1694/1694 | 1694 | t1: 512x512 (1694); t2: 512x512 (1694); gt: 512x512 (1694) |
| second | /guisongxia01/pangchao/wangyihan/datasets/lim1/SECOND | train | 2968/2968/2968 | 2968 | t1: 512x512 (2968); t2: 512x512 (2968); gt: 512x512 (2968) |

同一数据的原图、裁块和软链接分别登记；软链接解析到同一目录时只扫描一次，不跨表示相加。

| 数据集 | 未发现完整配对的划分 |
| --- | --- |
| sysu | val |
| levir | 无 |
| whu | val |
| second | val |

## SYSU val 原始航片分组

No complete SYSU val pairs found in the inspected local representations.

不能用重命名后的编号整除 25 推断原始航片。dataset_index 的空条目也不能证明 val 文件仍存在。

## SChanger

本地训练代码显示按 val 选择最优权重：True。
官方发布权重是否用 val 选过：未知，按“用过”处理。本地训练脚本不能证明官方权重的训练历史。
检查相关日志 0 个；证据文件与行号见 inventory.json。

| 权重路径 | 文件字节数 |
| --- | ---: |
| /guisongxia01/pangchao/wangyihan/Vlm_vs_OneJev/checkpoints/schanger_small_sysu.pth | 2721974 |
| /guisongxia01/pangchao/wangyihan/models/SChanger/schanger_base_levir.pth | 9845558 |
| /guisongxia01/pangchao/wangyihan/models/SChanger/schanger_base_sysu.pth | 9831935 |
| /guisongxia01/pangchao/wangyihan/models/SChanger/schanger_base_whu.pth | 9845558 |
| /guisongxia01/pangchao/wangyihan/models/SChanger/schanger_small_levir.pth | 2721974 |
| /guisongxia01/pangchao/wangyihan/models/SChanger/schanger_small_sysu.pth | 2721974 |
| /guisongxia01/pangchao/wangyihan/models/SChanger/schanger_small_whu.pth | 2708867 |

## 训练池可用性

未发现可用 SYSU val；仅有 train 可作后备，存在检测器过拟合偏差。记录此限制并停在盘点，不自动生成训练池或重训。

此次为本地盘点；未联网核实地面分辨率、未下载数据、未训练检测器、未生成数据划分。
