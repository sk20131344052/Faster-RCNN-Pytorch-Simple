import torch
import torch.nn as nn
import numpy as np

from utils_.utils import to_var, to_tensor

def rpn_loss(rpn_cls_prob, rpn_logits, rpn_bbox_pred, rpn_labels, rpn_bbox_targets, rpn_bbox_inside_weights):

    """
    Arguments:
        rpn_cls_prob (Tensor): (1, 2*9, H/16, W/16)
        rpn_logits (Tensor): (H/16 * W/16 , 2) object or non-object rpn_logits
        rpn_bbox_pred (Tensor): (1, 4*9, H/16, W/16) predicted boxes
        rpn_labels (Ndarray) : (H/16 * W/16 * 9 ,)
        rpn_bbox_targets (Ndarray) : (H/16 * W/16 * 9, 4)
        rpn_bbox_inside_weights (Ndarray) : (H/16 * W/16 * 9, 4) masking for only positive box loss

    Return:
        cls_loss (Scalar) : classfication loss
        reg_loss * 10 (Scalar) : regression loss
        log (Tuple) : for logging
    """

    height, width = rpn_cls_prob.size()[-2:]  # 14, 14

    # (1, 18, H/16, W/16) => (9, 2, H/16, W/16) => (9, H/16, W/16, 2) => (9 * H/16 * W/16, 2)
    rpn_cls_prob = rpn_cls_prob.view(-1, 2, height, width).permute(0, 2, 3, 1).contiguous().view(-1, 2)

    rpn_labels = torch.from_numpy(rpn_labels).long() # convert properly
    # (H/16 * W/16 * 9) => (1, H/16, W/16, 9) => (1, 9, H/16, W/16) => (9 * H/16 * W/16, )
    rpn_labels = rpn_labels.view(1, height, width, -1).permute(0, 3, 1, 2).contiguous()
    rpn_labels = rpn_labels.view(-1)

    # index where not -1
    idx = rpn_labels.ge(0).nonzero()[:, 0]
    rpn_cls_prob = rpn_cls_prob.index_select(0, to_var(idx))
    rpn_labels = rpn_labels.index_select(0, idx)
    rpn_logits = rpn_logits.squeeze().index_select(0, to_var(idx))


    # (H/16 * W/16, 4) => (1, H/16, W/16, 36) => (1, 36, H/16, W/16)
    rpn_bbox_targets = torch.from_numpy(rpn_bbox_targets)
    rpn_bbox_targets = rpn_bbox_targets.view(1, height, width, -1).permute(0, 3, 1, 2)
    rpn_bbox_targets = to_var(rpn_bbox_targets)

    po_cnt = torch.sum(rpn_labels.eq(1))
    ne_cnt = torch.sum(rpn_labels.eq(0))

    # for debug
    maxv, predict = rpn_cls_prob.data.max(1)
    

    po_idx = rpn_labels.eq(1).nonzero()[:, 0].cuda() if torch.cuda.is_available() else rpn_labels.eq(1).nonzero()[:, 0]
    ne_idx = rpn_labels.eq(0).nonzero()[:, 0].cuda() if torch.cuda.is_available() else rpn_labels.eq(0).nonzero()[:, 0]
    rpn_labels = rpn_labels.cuda() if torch.cuda.is_available() else rpn_labels

    tp = torch.sum(predict.index_select(0, po_idx).eq(rpn_labels.index_select(0, po_idx))) if po_cnt > 0 else 0
    tn = torch.sum(predict.index_select(0, ne_idx).eq(rpn_labels.index_select(0, ne_idx)))

    rpn_labels = to_var(rpn_labels)
    #cls_crit = nn.NLLLoss()
    #cls_loss = cls_crit(rpn_cls_prob, rpn_labels)

    cls_crit = nn.CrossEntropyLoss()
    cls_loss = cls_crit(rpn_logits, rpn_labels)

    # (H/16 * W/16, 4) => (1, H/16, W/16, 36) => (1, 36, H/16, W/16)
    rpn_bbox_inside_weights = torch.from_numpy(rpn_bbox_inside_weights)
    rpn_bbox_inside_weights = rpn_bbox_inside_weights.view(1, height, width, -1).permute(0, 3, 1, 2)
    rpn_bbox_inside_weights = rpn_bbox_inside_weights.cuda() if torch.cuda.is_available() else rpn_bbox_inside_weights     


    rpn_bbox_pred = to_var(torch.mul(rpn_bbox_pred.data, rpn_bbox_inside_weights))
    rpn_bbox_targets = to_var(torch.mul(rpn_bbox_targets.data, rpn_bbox_inside_weights))


    reg_crit = nn.SmoothL1Loss(size_average=False)
    reg_loss = reg_crit(rpn_bbox_pred, rpn_bbox_targets) / (po_cnt + 1e-4)

    log = (po_cnt, ne_cnt, tp, tn)
    return cls_loss, reg_loss * 10, log


def frcnn_loss(frcnn_cls_prob, frcnn_logits, frcnn_bbox_pred, frcnn_labels, frcnn_bbox_targets, frcnn_bbox_inside_weights):
    
    """
    Arguments:
        frcnn_cls_prob (Tensor): (256, 21) 21 class prob
        frcnn_logits (Tensor): (256 , 21) 21 class logtis
        frcnn_bbox_pred (Tensor): (256, 84) predicted boxes for 21 class
        frcnn_labels (Ndarray) : (256,)
        frcnn_bbox_targets (Ndarray) : (256, 84)
        frcnn_bbox_inside_weights (Ndarray) : (256, 84) masking for only foreground box loss

    Return:
        cls_loss (Scalar) : classfication loss
        reg_loss * 10 (Scalar) : regression loss
        log (Tuple) : for logging
    """


    frcnn_labels = to_tensor(frcnn_labels).long()
    fg_cnt = torch.sum(frcnn_labels.ne(0))
    bg_cnt = frcnn_labels.numel() - fg_cnt
    #print(fg_cnt, bg_cnt)

    maxv, predict = frcnn_cls_prob.data.max(1)
    tp = torch.sum(predict[:fg_cnt].eq(frcnn_labels[:fg_cnt])) if fg_cnt > 0 else 0
    tn = torch.sum(predict[fg_cnt:].eq(frcnn_labels[fg_cnt:]))


    ce_weights = torch.ones(frcnn_cls_prob.size()[1])
    ce_weights[0] = float(fg_cnt) / bg_cnt

    if torch.cuda.is_available():
        ce_weights = ce_weights.cuda()

    #cls_crit = nn.NLLLoss(weight=ce_weights)
    #cls_loss = cls_crit(frcnn_cls_prob, frcnn_labels)

    frcnn_labels = to_var(frcnn_labels)
    cls_crit = nn.CrossEntropyLoss(weight=ce_weights)
    cls_loss = cls_crit(frcnn_logits, frcnn_labels)

    frcnn_bbox_inside_weights = to_tensor(frcnn_bbox_inside_weights)
    frcnn_bbox_targets = to_tensor(frcnn_bbox_targets)

    frcnn_bbox_pred = to_var(torch.mul(frcnn_bbox_pred.data, frcnn_bbox_inside_weights))
    frcnn_bbox_targets = to_var(torch.mul(frcnn_bbox_targets, frcnn_bbox_inside_weights))

    reg_crit = nn.SmoothL1Loss(size_average=False)
    reg_loss = reg_crit(frcnn_bbox_pred, frcnn_bbox_targets)  / (fg_cnt + 1e-4)

    log = (fg_cnt, bg_cnt, tp, tn)

    return cls_loss, reg_loss * 10, log

