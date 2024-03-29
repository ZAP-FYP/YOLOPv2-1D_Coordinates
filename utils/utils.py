import datetime
import logging
import os
import platform
import subprocess
import time
from pathlib import Path
import re
import glob
import random
import cv2
import numpy as np
import torch
import torchvision
import matplotlib.pyplot as plt
import math
from scipy.spatial.distance import euclidean

logger = logging.getLogger(__name__)


def git_describe(path=Path(__file__).parent):  # path must be a directory
    # return human-readable git description, i.e. v5.0-5-g3e25f1e https://git-scm.com/docs/git-describe
    s = f'git -C {path} describe --tags --long --always'
    try:
        return subprocess.check_output(s, shell=True, stderr=subprocess.STDOUT).decode()[:-1]
    except subprocess.CalledProcessError as e:
        return ''  # not a git repository

def date_modified(path=__file__):
    # return human-readable file modification date, i.e. '2021-3-26'
    t = datetime.datetime.fromtimestamp(Path(path).stat().st_mtime)
    return f'{t.year}-{t.month}-{t.day}'

def select_device(device='', batch_size=None):
    # device = 'cpu' or '0' or '0,1,2,3'
    s = f'YOLOPv2 🚀 {git_describe() or date_modified()} torch {torch.__version__} '  # string
    cpu = device.lower() == 'cpu'
    if cpu:
        os.environ['CUDA_VISIBLE_DEVICES'] = '-1'  # force torch.cuda.is_available() = False
    elif device:  # non-cpu device requested
        os.environ['CUDA_VISIBLE_DEVICES'] = device  # set environment variable
        assert torch.cuda.is_available(), f'CUDA unavailable, invalid device {device} requested'  # check availability

    cuda = not cpu and torch.cuda.is_available()
    if cuda:
        n = torch.cuda.device_count()
        if n > 1 and batch_size:  # check that batch_size is compatible with device_count
            assert batch_size % n == 0, f'batch-size {batch_size} not multiple of GPU count {n}'
        space = ' ' * len(s)
        for i, d in enumerate(device.split(',') if device else range(n)):
            p = torch.cuda.get_device_properties(i)
            s += f"{'' if i == 0 else space}CUDA:{d} ({p.name}, {p.total_memory / 1024 ** 2}MB)\n"  # bytes to MB
    else:
        s += 'CPU\n'

    logger.info(s.encode().decode('ascii', 'ignore') if platform.system() == 'Windows' else s)  # emoji-safe
    return torch.device('cuda:0' if cuda else 'cpu')


def time_synchronized():
    # pytorch-accurate time
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    return time.time()

def plot_one_box(x, img, color=None, label=None, line_thickness=3):
    # Plots one bounding box on image img
    tl = line_thickness or round(0.002 * (img.shape[0] + img.shape[1]) / 2) + 1  # line/font thickness
    color = color or [random.randint(0, 255) for _ in range(3)]
    c1, c2 = (int(x[0]), int(x[1])), (int(x[2]), int(x[3]))
    cv2.rectangle(img, c1, c2, [0,255,255], thickness=2, lineType=cv2.LINE_AA)
    if label:
        tf = max(tl - 1, 1)  # font thickness
        t_size = cv2.getTextSize(label, 0, fontScale=tl / 3, thickness=tf)[0]
        c2 = c1[0] + t_size[0], c1[1] - t_size[1] - 3

class SegmentationMetric(object):
    '''
    imgLabel [batch_size, height(144), width(256)]
    confusionMatrix [[0(TN),1(FP)],
                     [2(FN),3(TP)]]
    '''
    def __init__(self, numClass):
        self.numClass = numClass
        self.confusionMatrix = np.zeros((self.numClass,)*2)

    def pixelAccuracy(self):
        # return all class overall pixel accuracy
        # acc = (TP + TN) / (TP + TN + FP + TN)
        acc = np.diag(self.confusionMatrix).sum() /  self.confusionMatrix.sum()
        return acc
        
    def lineAccuracy(self):
        Acc = np.diag(self.confusionMatrix) / (self.confusionMatrix.sum(axis=1) + 1e-12)
        return Acc[1]

    def classPixelAccuracy(self):
        # return each category pixel accuracy(A more accurate way to call it precision)
        # acc = (TP) / TP + FP
        classAcc = np.diag(self.confusionMatrix) / (self.confusionMatrix.sum(axis=0) + 1e-12)
        return classAcc

    def meanPixelAccuracy(self):
        classAcc = self.classPixelAccuracy()
        meanAcc = np.nanmean(classAcc)
        return meanAcc

    def meanIntersectionOverUnion(self):
        # Intersection = TP Union = TP + FP + FN
        # IoU = TP / (TP + FP + FN)
        intersection = np.diag(self.confusionMatrix)
        union = np.sum(self.confusionMatrix, axis=1) + np.sum(self.confusionMatrix, axis=0) - np.diag(self.confusionMatrix)
        IoU = intersection / union
        IoU[np.isnan(IoU)] = 0
        mIoU = np.nanmean(IoU)
        return mIoU
    
    def IntersectionOverUnion(self):
        intersection = np.diag(self.confusionMatrix)
        union = np.sum(self.confusionMatrix, axis=1) + np.sum(self.confusionMatrix, axis=0) - np.diag(self.confusionMatrix)
        IoU = intersection / union
        IoU[np.isnan(IoU)] = 0
        return IoU[1]

    def genConfusionMatrix(self, imgPredict, imgLabel):
        # remove classes from unlabeled pixels in gt image and predict
        # print(imgLabel.shape)
        mask = (imgLabel >= 0) & (imgLabel < self.numClass)
        label = self.numClass * imgLabel[mask] + imgPredict[mask]
        count = np.bincount(label, minlength=self.numClass**2)
        confusionMatrix = count.reshape(self.numClass, self.numClass)
        return confusionMatrix

    def Frequency_Weighted_Intersection_over_Union(self):
        # FWIOU =     [(TP+FN)/(TP+FP+TN+FN)] *[TP / (TP + FP + FN)]
        freq = np.sum(self.confusionMatrix, axis=1) / np.sum(self.confusionMatrix)
        iu = np.diag(self.confusionMatrix) / (
                np.sum(self.confusionMatrix, axis=1) + np.sum(self.confusionMatrix, axis=0) -
                np.diag(self.confusionMatrix))
        FWIoU = (freq[freq > 0] * iu[freq > 0]).sum()
        return FWIoU


    def addBatch(self, imgPredict, imgLabel):
        assert imgPredict.shape == imgLabel.shape
        self.confusionMatrix += self.genConfusionMatrix(imgPredict, imgLabel)

    def reset(self):
        self.confusionMatrix = np.zeros((self.numClass, self.numClass))

class AverageMeter(object):
    """Computes and stores the average and current value"""
    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count if self.count != 0 else 0

def _make_grid(nx=20, ny=20):
        yv, xv = torch.meshgrid([torch.arange(ny), torch.arange(nx)])
        return torch.stack((xv, yv), 2).view((1, 1, ny, nx, 2)).float()
    
def split_for_trace_model(pred = None, anchor_grid = None):
    z = []
    st = [8,16,32]
    for i in range(3):
        bs, _, ny, nx = pred[i].shape  
        pred[i] = pred[i].view(bs, 3, 85, ny, nx).permute(0, 1, 3, 4, 2).contiguous()
        y = pred[i].sigmoid()
        gr = _make_grid(nx, ny).to(pred[i].device)
        y[..., 0:2] = (y[..., 0:2] * 2. - 0.5 + gr) * st[i]  # xy
        y[..., 2:4] = (y[..., 2:4] * 2) ** 2 * anchor_grid[i]  # wh
        z.append(y.view(bs, -1, 85))
    pred = torch.cat(z, 1)
    return pred

def show_seg_result(img, result, palette=None,is_demo=False, edge_thickness=3):

    if palette is None:
        palette = np.random.randint(
                0, 255, size=(3, 3))
    palette[0] = [0, 0, 0]
    palette[1] = [0, 255, 0]
    palette[2] = [255, 0, 0]
    palette = np.array(palette)
    assert palette.shape[0] == 3 # len(classes)
    assert palette.shape[1] == 3
    assert len(palette.shape) == 2
    
    if not is_demo:
        # color_seg = np.zeros((result.shape[0], result.shape[1], 3), dtype=np.uint8)
        color_seg = np.zeros((result.shape[0], result.shape[1], 3), dtype=np.uint8)
        for label, color in enumerate(palette):
            color_seg[result == label, :] = color
    else:
        color_area = np.zeros((result[0].shape[0], result[0].shape[1], 3), dtype=np.uint8)
        #
        # color_area[result[0] == 1] = [0, 255, 0]
        # #-- This is realted to line detection color_area[result[1] ==1] = [255, 0, 0]
        # color_seg = color_area
        print(f"result222 {len(result[0][0]), len(result[0])}")
        edge_pixels = cv2.Canny(result[0].astype(np.uint8), 0, 1)

        # print(get_drivable_area_in_1D(edge_pixels))
        dilated_edges = cv2.dilate(edge_pixels, None, iterations=edge_thickness)
        color_area[dilated_edges != 0] = [0, 255, 0]
        color_seg = color_area

    # convert to BGR
    color_seg = color_seg[..., ::-1]
    # print(color_seg.shape)
    color_mask = np.mean(color_seg, 2)
    # img[color_mask != 0] = img[color_mask != 0] * 0.5 + color_seg[color_mask != 0] * 0.5
    # img = img * 0.5 + color_seg * 0.5
    #img = img.astype(np.uint8)
    #img = cv2.resize(img, (1280,720), interpolation=cv2.INTER_LINEAR)
    # Define the new width (e.g., 600)
    new_width = 600

    # Calculate the scaling factor to maintain the aspect ratio
    scale_factor = new_width / edge_pixels.shape[1]
    current_width = edge_pixels.shape[1]
    current_height = edge_pixels.shape[0]

    # Calculate the new height
    new_height = int(edge_pixels.shape[0] * scale_factor)

    # Resize the edge_pixels array
    resized_edge_pixels = cv2.resize(edge_pixels, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
        # Set a threshold value (typically, 127 is used for binary images)
    threshold_value = 127

    # Apply thresholding to convert to a binary image
    _, binary_edge_pixels = cv2.threshold(resized_edge_pixels, threshold_value, 255, cv2.THRESH_BINARY)
    print(f"edge_pixels.shape {edge_pixels.shape}")

    print(f"edge_pixels.unique {np.unique(edge_pixels, return_counts=True)}")

    print(f"binary_edge_pixels.shape {binary_edge_pixels.shape}")
    print(f"binary_edge_pixels.unique {np.unique(binary_edge_pixels, return_counts=True)}")

    # resized_edge_pixels now contains the downsampled image with a width of 600 pixels while maintaining the aspect ratio.
    return edge_pixels, current_width, current_height


def get_drivable_area_in_1D(segmentation_matrix, current_width, current_height):

    # discretized_matrix = discretize_width(segmentation_matrix)
    # polygon_coords = convert_seg_to_arr(discretized_matrix)

    polygon_coords = convert_seg_to_arr(segmentation_matrix)
    print(f"len(polygon_coords) {len(polygon_coords)}")
    if len(polygon_coords) == 0:
        polygon_coords = [(i, 0) for i in range(current_width + 1)]
        print(f"empty len(polygon_coords) {len(polygon_coords)}")

    # print(f"polygon_coords {polygon_coords}")

    # Separate x and y coordinates
    x_values = [coord[0] for coord in polygon_coords]
    y_values = [coord[1] for coord in polygon_coords]

    # Create a scatter plot
    plt.scatter(x_values, y_values, marker='o', color='blue', s=2,label='convert_seg_to_arr')

    # Add labels and a legend
    plt.xlabel('X-coordinate')
    plt.ylabel('Y-coordinate')

    # plt.legend()
    # plt.show()

    # plt.figure(figsize=(8, 4))
    # plt.plot(polygon_coords, label="convert_seg_to_arr")
    plt.savefig(os.path.join("visualizations/convert_seg_to_arr", f"sample.png"))
    plt.close()
    # print(polygon_coords)
    # Create a dictionary to store the maximum Y value for each X value
    
    filtered_polygon_coords = remove_bottom_edge(polygon_coords)
    x_values = [coord[0] for coord in filtered_polygon_coords]
    y_values = [coord[1] for coord in filtered_polygon_coords]
     # Create a scatter plot
    plt.scatter(x_values, y_values, marker='o', color='blue',s=2, label='remove_bottom_edge')

    # Add labels and a legend
    plt.xlabel('X-coordinate')
    plt.ylabel('Y-coordinate')
    # print(f"filtered_polygon_coords.shape {len(filtered_polygon_coords)}")
    # plt.figure(figsize=(8, 4))
    # plt.plot(filtered_polygon_coords, label="remove_bottom_edge")
    plt.savefig(os.path.join("visualizations/remove_bottom_edge", f"sample.png"))
    plt.close()

    # updated_coords = fill_missing_coords(filtered_polygon_coords, segmentation_matrix.shape[1], segmentation_matrix.shape[0])
    updated_coords= fill_missing_coords(filtered_polygon_coords, current_width, current_height)
    x_values = [coord[0] for coord in updated_coords]
    y_values = [coord[1] for coord in updated_coords]
     # Create a scatter plot
    plt.scatter(x_values, y_values, marker='o', color='blue',s=2, label='remove_bottom_edge')

    # Add labels and a legend
    plt.xlabel('X-coordinate')
    plt.ylabel('Y-coordinate')
    print(f"updated_coords.shape {len(updated_coords)}")
    # plt.figure(figsize=(8, 4))
    # plt.plot(updated_coords, label="fill_missing_coords")
    plt.savefig(os.path.join("visualizations/fill_missing_coords", f"sample.png"))
    plt.close()

    width_N = len(updated_coords) // 100
    # print(len(updated_coords))
    # print(width_N)
    # skipped_y = updated_coords[::width_N]
    # print("skipped array", skipped_y)

    discretized_coords = []

    for i in range(0, len(updated_coords), width_N):
        chunk = updated_coords[i:i + width_N]
        mean_y = int(round(np.mean([y for _, y in chunk])))
        x_coordinates = [x for x, _ in chunk]
        # median_x = x_coordinates[width_N]
        median_x = int(round(np.median(x_coordinates)))  
        discretized_coords.append((median_x, mean_y))

    y_coords = [coord[1] for coord in discretized_coords]

    # y_coords = get_1D_arr(updated_coords, len(segmentation_matrix[0]), segmentation_matrix.shape[0])
    # print(y_coords)

    # Extract X and Y coordinates
    x_coords_plot = [coord[0] for coord in discretized_coords]
    y_coords_plot = [coord[1] for coord in discretized_coords]

    # print(len(x_coords_plot))
    # print(len(y_coords))

    # Create a scatter plot
    plt.scatter(x_coords_plot, y_coords_plot, c='blue', marker='o', label='Points')
    plt.xlabel('X Coordinate')
    plt.ylabel('Y Coordinate')
    plt.title('Visualization of polygon 2D Coordinates')
    # plt.show()
    # plt.gca().invert_yaxis()
    plt.savefig("polygon_coords_2.jpg")

    # print(len(y_coords))
    return y_coords
    


def increment_path(path, exist_ok=True, sep=''):
    # Increment path, i.e. runs/exp --> runs/exp{sep}0, runs/exp{sep}1 etc.
    path = Path(path)  # os-agnostic
    if (path.exists() and exist_ok) or (not path.exists()):
        return str(path)
    else:
        dirs = glob.glob(f"{path}{sep}*")  # similar paths
        matches = [re.search(rf"%s{sep}(\d+)" % path.stem, d) for d in dirs]
        i = [int(m.groups()[0]) for m in matches if m]  # indices
        n = max(i) + 1 if i else 2  # increment number
        return f"{path}{sep}{n}"  # update path

def scale_coords(img1_shape, coords, img0_shape, ratio_pad=None):
    # Rescale coords (xyxy) from img1_shape to img0_shape
    if ratio_pad is None:  # calculate from img0_shape
        gain = min(img1_shape[0] / img0_shape[0], img1_shape[1] / img0_shape[1])  # gain  = old / new
        pad = (img1_shape[1] - img0_shape[1] * gain) / 2, (img1_shape[0] - img0_shape[0] * gain) / 2  # wh padding
    else:
        gain = ratio_pad[0][0]
        pad = ratio_pad[1]

    coords[:, [0, 2]] -= pad[0]  # x padding
    coords[:, [1, 3]] -= pad[1]  # y padding
    coords[:, :4] /= gain
    clip_coords(coords, img0_shape)
    return coords


def clip_coords(boxes, img_shape):
    # Clip bounding xyxy bounding boxes to image shape (height, width)
    boxes[:, 0].clamp_(0, img_shape[1])  # x1
    boxes[:, 1].clamp_(0, img_shape[0])  # y1
    boxes[:, 2].clamp_(0, img_shape[1])  # x2
    boxes[:, 3].clamp_(0, img_shape[0])  # y2

def set_logging(rank=-1):
    logging.basicConfig(
        format="%(message)s",
        level=logging.INFO if rank in [-1, 0] else logging.WARN)

def xywh2xyxy(x):
    # Convert nx4 boxes from [x, y, w, h] to [x1, y1, x2, y2] where xy1=top-left, xy2=bottom-right
    y = x.clone() if isinstance(x, torch.Tensor) else np.copy(x)
    y[:, 0] = x[:, 0] - x[:, 2] / 2  # top left x
    y[:, 1] = x[:, 1] - x[:, 3] / 2  # top left y
    y[:, 2] = x[:, 0] + x[:, 2] / 2  # bottom right x
    y[:, 3] = x[:, 1] + x[:, 3] / 2  # bottom right y
    return y

def xyxy2xywh(x):
    # Convert nx4 boxes from [x1, y1, x2, y2] to [x, y, w, h] where xy1=top-left, xy2=bottom-right
    y = x.clone() if isinstance(x, torch.Tensor) else np.copy(x)
    y[:, 0] = (x[:, 0] + x[:, 2]) / 2  # x center
    y[:, 1] = (x[:, 1] + x[:, 3]) / 2  # y center
    y[:, 2] = x[:, 2] - x[:, 0]  # width
    y[:, 3] = x[:, 3] - x[:, 1]  # height
    return y

def non_max_suppression(prediction, conf_thres=0.25, iou_thres=0.45, classes=None, agnostic=False, multi_label=False,
                        labels=()):
    """Runs Non-Maximum Suppression (NMS) on inference results

    Returns:
         list of detections, on (n,6) tensor per image [xyxy, conf, cls]
    """

    nc = prediction.shape[2] - 5  # number of classes
    xc = prediction[..., 4] > conf_thres  # candidates

    # Settings
    min_wh, max_wh = 2, 4096  # (pixels) minimum and maximum box width and height
    max_det = 300  # maximum number of detections per image
    max_nms = 30000  # maximum number of boxes into torchvision.ops.nms()
    time_limit = 10.0  # seconds to quit after
    redundant = True  # require redundant detections
    multi_label &= nc > 1  # multiple labels per box (adds 0.5ms/img)
    merge = False  # use merge-NMS

    t = time.time()
    output = [torch.zeros((0, 6), device=prediction.device)] * prediction.shape[0]
    for xi, x in enumerate(prediction):  # image index, image inference
        # Apply constraints
        # x[((x[..., 2:4] < min_wh) | (x[..., 2:4] > max_wh)).any(1), 4] = 0  # width-height
        x = x[xc[xi]]  # confidence

        # Cat apriori labels if autolabelling
        if labels and len(labels[xi]):
            l = labels[xi]
            v = torch.zeros((len(l), nc + 5), device=x.device)
            v[:, :4] = l[:, 1:5]  # box
            v[:, 4] = 1.0  # conf
            v[range(len(l)), l[:, 0].long() + 5] = 1.0  # cls
            x = torch.cat((x, v), 0)

        # If none remain process next image
        if not x.shape[0]:
            continue

        # Compute conf
        x[:, 5:] *= x[:, 4:5]  # conf = obj_conf * cls_conf

        # Box (center x, center y, width, height) to (x1, y1, x2, y2)
        box = xywh2xyxy(x[:, :4])

        # Detections matrix nx6 (xyxy, conf, cls)
        if multi_label:
            i, j = (x[:, 5:] > conf_thres).nonzero(as_tuple=False).T
            x = torch.cat((box[i], x[i, j + 5, None], j[:, None].float()), 1)
        else:  # best class only
            conf, j = x[:, 5:].max(1, keepdim=True)
            x = torch.cat((box, conf, j.float()), 1)[conf.view(-1) > conf_thres]

        # Filter by class
        if classes is not None:
            x = x[(x[:, 5:6] == torch.tensor(classes, device=x.device)).any(1)]

        # Apply finite constraint
        # if not torch.isfinite(x).all():
        #     x = x[torch.isfinite(x).all(1)]

        # Check shape
        n = x.shape[0]  # number of boxes
        if not n:  # no boxes
            continue
        elif n > max_nms:  # excess boxes
            x = x[x[:, 4].argsort(descending=True)[:max_nms]]  # sort by confidence

        # Batched NMS
        c = x[:, 5:6] * (0 if agnostic else max_wh)  # classes
        boxes, scores = x[:, :4] + c, x[:, 4]  # boxes (offset by class), scores
        i = torchvision.ops.nms(boxes, scores, iou_thres)  # NMS
        if i.shape[0] > max_det:  # limit detections
            i = i[:max_det]
        if merge and (1 < n < 3E3):  # Merge NMS (boxes merged using weighted mean)
            # update boxes as boxes(i,4) = weights(i,n) * boxes(n,4)
            iou = box_iou(boxes[i], boxes) > iou_thres  # iou matrix
            weights = iou * scores[None]  # box weights
            x[i, :4] = torch.mm(weights, x[:, :4]).float() / weights.sum(1, keepdim=True)  # merged boxes
            if redundant:
                i = i[iou.sum(1) > 1]  # require redundancy

        output[xi] = x[i]
        if (time.time() - t) > time_limit:
            print(f'WARNING: NMS time limit {time_limit}s exceeded')
            break  # time limit exceeded

    return output

def box_iou(box1, box2):
    # https://github.com/pytorch/vision/blob/master/torchvision/ops/boxes.py
    """
    Return intersection-over-union (Jaccard index) of boxes.
    Both sets of boxes are expected to be in (x1, y1, x2, y2) format.
    Arguments:
        box1 (Tensor[N, 4])
        box2 (Tensor[M, 4])
    Returns:
        iou (Tensor[N, M]): the NxM matrix containing the pairwise
            IoU values for every element in boxes1 and boxes2
    """

    def box_area(box):
        # box = 4xn
        return (box[2] - box[0]) * (box[3] - box[1])

    area1 = box_area(box1.T)
    area2 = box_area(box2.T)

    # inter(N,M) = (rb(N,M,2) - lt(N,M,2)).clamp(0).prod(2)
    inter = (torch.min(box1[:, None, 2:], box2[:, 2:]) - torch.max(box1[:, None, :2], box2[:, :2])).clamp(0).prod(2)
    return inter / (area1[:, None] + area2 - inter)  # iou = inter / (area1 + area2 - inter)

class LoadImages:  # for inference
    def __init__(self, path, img_size=640, stride=32):
        p = str(Path(path).absolute())  # os-agnostic absolute path
        if '*' in p:
            files = sorted(glob.glob(p, recursive=True))  # glob
        elif os.path.isdir(p):
            files = sorted(glob.glob(os.path.join(p, '*.*')))  # dir
        elif os.path.isfile(p):
            files = [p]  # files
        else:
            raise Exception(f'ERROR: {p} does not exist')

        img_formats = ['bmp', 'jpg', 'jpeg', 'png', 'tif', 'tiff', 'dng', 'webp', 'mpo']  # acceptable image suffixes
        vid_formats = ['mov', 'avi', 'mp4', 'mpg', 'mpeg', 'm4v', 'wmv', 'mkv']  # acceptable video suffixes
        images = [x for x in files if x.split('.')[-1].lower() in img_formats]
        videos = [x for x in files if x.split('.')[-1].lower() in vid_formats]
        ni, nv = len(images), len(videos)

        self.img_size = img_size//2
        self.stride = stride
        self.files = images + videos
        self.filename = ''
        self.nf = ni + nv  # number of files
        self.video_flag = [False] * ni + [True] * nv
        self.mode = 'image'
        if any(videos):
            self.new_video(videos[0])  # new video
        else:
            self.cap = None
        assert self.nf > 0, f'No images or videos found in {p}. ' \
                            f'Supported formats are:\nimages: {img_formats}\nvideos: {vid_formats}'

    def __iter__(self):
        self.count = 0
        return self

    def __next__(self):
        if self.count == self.nf:
            raise StopIteration
        path = self.files[self.count]
        self.filename = os.path.splitext(os.path.basename(path))[0]

        if self.video_flag[self.count]:
            # Read video
            self.mode = 'video'
            ret_val, img0 = self.cap.read()
            if not ret_val:
                self.count += 1
                self.cap.release()
                if self.count == self.nf:  # last video
                    raise StopIteration
                else:
                    path = self.files[self.count]
                    self.new_video(path)
                    ret_val, img0 = self.cap.read()

            self.frame += 1
            print(f'video {self.count + 1}/{self.nf} ({self.frame}/{self.nframes}) {path}: ', end='')

        else:
            # Read image
            self.count += 1
            img0 = cv2.imread(path)  # BGR
            assert img0 is not None, 'Image Not Found ' + path
            #print(f'image {self.count}/{self.nf} {path}: ', end='')

        # Padded resize
        # img0 = cv2.resize(img0, (1280,720), interpolation=cv2.INTER_LINEAR)
        img0 = cv2.resize(img0, (img0.shape[1]//2, img0.shape[0]//2), interpolation=cv2.INTER_LINEAR)

        img = letterbox(img0, self.img_size, stride=self.stride)[0]

        # Convert
        img = img[:, :, ::-1].transpose(2, 0, 1)  # BGR to RGB, to 3x416x416
        img = np.ascontiguousarray(img)

        return path, img, img0, self.cap

    def new_video(self, path):
        self.frame = 0
        self.cap = cv2.VideoCapture(path)
        self.nframes = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))

    def __len__(self):
        return self.nf  # number of files

def letterbox(img, new_shape=(640, 640), color=(114, 114, 114), auto=True, scaleFill=False, scaleup=True, stride=32):
    # Resize and pad image while meeting stride-multiple constraints
    shape = img.shape[:2]  # current shape [height, width]
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)
    #print(sem_img.shape)
    # Scale ratio (new / old)
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])

    if not scaleup:  # only scale down, do not scale up (for better test mAP)
        r = min(r, 1.0)

    # Compute padding
    ratio = r, r  # width, height ratios
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]  # wh padding
    if auto:  # minimum rectangle
        dw, dh = np.mod(dw, stride), np.mod(dh, stride)  # wh padding
    elif scaleFill:  # stretch
        dw, dh = 0.0, 0.0
        new_unpad = (new_shape[1], new_shape[0])
        ratio = new_shape[1] / shape[1], new_shape[0] / shape[0]  # width, height ratios

    dw /= 2  # divide padding into 2 sides
    dh /= 2

    if shape[::-1] != new_unpad:  # resize
        img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
     
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))

    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)  # add border
    
    return img, ratio, (dw, dh)

def driving_area_mask(seg = None):
    da_predict = seg[:, :, 12:372,:]
    da_seg_mask = torch.nn.functional.interpolate(da_predict, scale_factor=2, mode='bilinear')
    _, da_seg_mask = torch.max(da_seg_mask, 1)
    da_seg_mask = da_seg_mask.int().squeeze().cpu().numpy()
    return da_seg_mask

def lane_line_mask(ll = None):
    ll_predict = ll[:, :, 12:372,:]
    ll_seg_mask = torch.nn.functional.interpolate(ll_predict, scale_factor=2, mode='bilinear')
    ll_seg_mask = torch.round(ll_seg_mask).squeeze(1)
    ll_seg_mask = ll_seg_mask.int().squeeze().cpu().numpy()
    return ll_seg_mask


def discretize_width(segmentation_matrix):
    step_width = segmentation_matrix.shape[1] // 100
    downsampled_matrix = np.zeros((segmentation_matrix.shape[0], 100))

    # Populate the downsampled matrix by averaging nearby points
    for i in range(segmentation_matrix.shape[0]):
        for j in range(100):
            start_col, end_col = j * step_width, (j + 1) * step_width
            subarray = segmentation_matrix[i, start_col:end_col]
            downsampled_matrix[i, j] = np.median(subarray)

    # print(downsampled_matrix)
    return downsampled_matrix


def convert_seg_to_arr(polygon):
    # edge polygon has 255. convert that to 1 into a binary matrix
    binary_matrix = polygon / 255
    # print(binary_matrix.shape)

    # Find edge pixels and convert to coordinates
    polygon_coordinates = []
    for y in range(binary_matrix.shape[0]):
        for x in range(binary_matrix.shape[1]):
            if binary_matrix[y, x] == 1:
                # If your coordinate system has (0,0) at the top-left
                # you might need to transform y as height - y - 1
                temp_y = binary_matrix.shape[0] - y - 1
                polygon_coordinates.append([x, temp_y])

    # polygon_coordinates = [(y, x) for x, y in np.argwhere(polygon)]
    # print(polygon_coordinates)


    return polygon_coordinates


def remove_bottom_edge(polygon_coords):
    max_y_values = {}
    for x, y in polygon_coords:
        if x in max_y_values:
            max_y_values[x] = max(max_y_values[x], y)
        else:
            max_y_values[x] = y

    # Filter out points with minimum Y value for each X value
    filtered_polygon_coords = [(x, y) for x, y in polygon_coords if y == max_y_values[x]]
    return filtered_polygon_coords

def random_sample(coordinates, n_sample):
    print(f"coordinates length {len(coordinates)} n_sample {n_sample}")
    return random.sample(coordinates, n_sample)

def regular_sample(coordinates, n_sample):
    
    step = len(coordinates) // n_sample
    sampled_indices = [i * step for i in range(n_sample)]
    sampled_elements = [coordinates[i] for i in sampled_indices]
    return sampled_elements

def farthest_points(coords, num_points):
    selected = [coords[0]]
    while len(selected) < num_points:
        max_dist = -1
        farthest_point = None
        for coord in coords:
            if coord not in selected:
                min_dist = min(euclidean(coord, s) for s in selected)
                if min_dist > max_dist:
                    max_dist = min_dist
                    farthest_point = coord
        selected.append(farthest_point)
    return selected

def fill_missing_coords(filtered_polygon_coords, max_x, max_y):
    # Sort the coordinates by X coordinates

    sorted_coords = sorted(filtered_polygon_coords, key=lambda coord: coord[0])
    # print(f"sorted_coords[0], filtered_polygon_coords[0]  {sorted_coords[0], filtered_polygon_coords[0]}")

    # Create a set of existing x-values for faster lookup
    existing_x_values = set(coord[0] for coord in sorted_coords)

    # Initialize the list to store the updated coordinates
    updated_coords = []
    print(f"sorted_coords[0][0], max_x {sorted_coords[0][0], max_x}")
    # for x in range(sorted_coords[0][0], max_x):
    for x in range(0, max_x):

        if x in existing_x_values:
            # If the x-value exists in your sorted list, keep the original coordinate
            y_value = next(coord[1] for coord in sorted_coords if coord[0] == x)
            updated_coords.append([x, y_value])
        else:
            # If the x-value is missing, add a new coordinate with y=360
            updated_coords.append([x, 0])
    desired_width = math.floor(max_x / 100) * 100
    desired_height = (desired_width/max_x)*max_y
    print(f"current_width, desired_width {max_x, desired_width}")

    sampled_coordinates = regular_sample(updated_coords, 100)
    
    sorted_sampled_coords = sorted(sampled_coordinates, key=lambda coord: coord[0])

    print(f"len(sampled_coordinates) {len(sampled_coordinates)}")
    print(f"len(updated_coords) {len(updated_coords)}")
    print(f"len(sorted_sampled_coords) {len(sorted_sampled_coords)}")

    return sorted_sampled_coords 


# def get_1D_arr(polygon_coords, arr_size, max_y):
#     # Sort the coordinates by X coordinates
#     sorted_coords = sorted(polygon_coords, key=lambda coord: coord[0])

#     # Initialize the result array with None values
#     result_arr = [0] * arr_size
    
#     # Iterate through the sorted coordinates and fill in the result array
#     for coord in sorted_coords:
#         x, y = coord
#         if 0 <= x < arr_size:  # Make sure the coordinate is within the array bounds
#             result_arr[x] = y

#     # y_coords = [coord[1] for coord in sorted_coords]

#     # return y_coords
#     # print("1D arr", len(result_arr))

#     return result_arr
