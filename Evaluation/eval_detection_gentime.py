import json
import numpy as np
import pandas as pd

from utils import get_blocked_videos
from utils import interpolated_prec_rec
from utils import segment_iou

class ANETdetection(object):

    GROUND_TRUTH_FIELDS = ['database']
    PREDICTION_FIELDS = ['results', 'version', 'external_data']

    def __init__(self, opt, ground_truth_filename=None, prediction_filename=None,
                 ground_truth_fields=GROUND_TRUTH_FIELDS,
                 prediction_fields=PREDICTION_FIELDS,
                 tiou_thresholds=np.linspace(0.5, 0.95, 10), 
                 subset='validation', verbose=False, 
                 check_status=True):
        if not ground_truth_filename:
            raise IOError('Please input a valid ground truth file.')
        if not prediction_filename:
            raise IOError('Please input a valid prediction file.')
        self.subset = subset
        self.tiou_thresholds = tiou_thresholds
        self.verbose = verbose
        self.gt_fields = ground_truth_fields
        self.pred_fields = prediction_fields
        self.ap = None
        self.tdiff = None
        self.check_status = check_status
        self.num_class = opt["num_of_class"]
        # Retrieve blocked videos from server.
        if self.check_status:
            self.blocked_videos = get_blocked_videos()
        else:
            self.blocked_videos = list()
        # Import ground truth and predictions.
        self.ground_truth, self.activity_index, cidx = self._import_ground_truth(
            ground_truth_filename)
        self.prediction = self._import_prediction(prediction_filename, cidx)

        if self.verbose:
            print('[INIT] Loaded annotations from {} subset.'.format(subset))
            nr_gt = len(self.ground_truth)
            print('\tNumber of ground truth instances: {}'.format(nr_gt))
            nr_pred = len(self.prediction)
            print('\tNumber of predictions: {}'.format(nr_pred))
            print('\tFixed threshold for tiou score: {}'.format(self.tiou_thresholds))

    def _import_ground_truth(self, ground_truth_filename):
        """Reads ground truth file, checks if it is well formatted, and returns
           the ground truth instances and the activity classes.

        Parameters
        ----------
        ground_truth_filename : str
            Full path to the ground truth json file.

        Outputs
        -------
        ground_truth : df
            Data frame containing the ground truth instances.
        activity_index : dict
            Dictionary containing class index.
        """
        with open(ground_truth_filename, 'r') as fobj:
            data = json.load(fobj)
        # Checking format
        if not all([field in list(data.keys()) for field in self.gt_fields]):
            raise IOError('Please input a valid ground truth file.')

        # Read ground truth data.
        activity_index, cidx = {}, 0

        video_lst, t_start_lst, t_end_lst, label_lst = [], [], [], []
        for videoid, v in data['database'].items():
            if self.subset not in v['subset']:
                continue
 
            for ann in v['annotations']:
                if ann['label'] not in activity_index:
                    activity_index[ann['label']] = cidx
                    cidx += 1
                video_lst.append(videoid)
                t_start_lst.append(ann['segment'][0])
                t_end_lst.append(ann['segment'][1])
                label_lst.append(activity_index[ann['label']])
        
        ground_truth = pd.DataFrame({'video-id': video_lst,
                                     't-start': t_start_lst,
                                     't-end': t_end_lst,
                                     'label': label_lst})

        return ground_truth, activity_index, cidx

    def _import_prediction(self, prediction_filename, cidx):
        """Reads prediction file, checks if it is well formatted, and returns
           the prediction instances.

        Parameters
        ----------
        prediction_filename : str
            Full path to the prediction json file.

        Outputs
        -------
        prediction : df
            Data frame containing the prediction instances.
        """
        with open(prediction_filename, 'r') as fobj:
            data = json.load(fobj)
        # Checking format...
        if not all([field in list(data.keys()) for field in self.pred_fields]):
            raise IOError('Please input a valid prediction file.')

        # Read predicitons.
        video_lst, t_start_lst, t_end_lst = [], [], []
        label_lst, score_lst = [], []
        gentime_lst = []
        for videoid, v in data['results'].items():
            if videoid in self.blocked_videos:
                continue
            for result in v:
                if result['label'] not in self.activity_index.keys():
                    continue

                label = self.activity_index[result['label']]
                video_lst.append(videoid)
                t_start_lst.append(result['segment'][0])
                t_end_lst.append(result['segment'][1])
                label_lst.append(label)
                score_lst.append(result['score'])
                gentime_lst.append(result['gentime'])

        prediction = pd.DataFrame({'video-id': video_lst,
                                   't-start': t_start_lst,
                                   't-end': t_end_lst,
                                   'label': label_lst,
                                   'score': score_lst,
                                   'gentime': gentime_lst})
        return prediction

    def wrapper_compute_average_precision(self):
        """Computes average precision for each class in the subset.
        """
        ap = np.zeros((len(self.tiou_thresholds), len(list(self.activity_index.items()))))
        tdiff = np.zeros((len(self.tiou_thresholds), len(list(self.activity_index.items()))))
        cnt_tp = np.zeros((len(self.tiou_thresholds), len(list(self.activity_index.items()))))
        
        for activity, cidx in self.activity_index.items():
            gt_idx = self.ground_truth['label'] == cidx
            pred_idx = self.prediction['label'] == cidx
            ap[:,cidx], tdiff[:,cidx], cnt_tp[:,cidx] = compute_average_precision_detection(
                self.ground_truth.loc[gt_idx].reset_index(drop=True),
                self.prediction.loc[pred_idx].reset_index(drop=True),
                tiou_thresholds=self.tiou_thresholds)
                
        sum_tdiff = np.sum(tdiff, axis=1)
        total_tp = np.sum(cnt_tp, axis=1)
        
        # FIX: Handle division by zero and NaN values
        final_tdiff = np.zeros_like(sum_tdiff)
        valid_mask = total_tp > 0
        final_tdiff[valid_mask] = sum_tdiff[valid_mask] / total_tp[valid_mask]
        
        # Handle NaN or Inf values
        final_tdiff = np.nan_to_num(final_tdiff, nan=0.0, posinf=0.0, neginf=0.0)
        
        return ap, final_tdiff

    def evaluate(self):
        """Evaluates a prediction file. For the detection task we measure the
        interpolated mean average precision to measure the performance of a
        method.
        """
        self.ap, self.tdiff = self.wrapper_compute_average_precision()
        self.mAP = self.ap.mean(axis=1)
        if self.verbose:
            print('[RESULTS] Performance on ActivityNet detection task.')
            print('\tAverage-mAP: {}'.format(self.mAP.mean()))
            print('\tAverage-time diff: {}'.format(self.tdiff.mean()))

def compute_average_precision_detection(ground_truth, prediction, tiou_thresholds=np.linspace(0.5, 0.95, 10)):
    """Compute average precision (detection task) between ground truth and
    predictions data frames. If multiple predictions occurs for the same
    predicted segment, only the one with highest score is matches as
    true positive. This code is greatly inspired by Pascal VOC devkit.

    Parameters
    ----------
    ground_truth : df
        Data frame containing the ground truth instances.
        Required fields: ['video-id', 't-start', 't-end']
    prediction : df
        Data frame containing the prediction instances.
        Required fields: ['video-id, 't-start', 't-end', 'score']
    tiou_thresholds : 1darray, optional
        Temporal intersection over union threshold.

    Outputs
    -------
    ap : float
        Average precision score.
    """
    # Handle empty predictions or ground truth
    if len(prediction) == 0 or len(ground_truth) == 0:
        ap = np.zeros(len(tiou_thresholds))
        tdiff = np.zeros(len(tiou_thresholds))
        cnt_tp = np.zeros(len(tiou_thresholds))
        return ap, tdiff, cnt_tp
    
    npos = float(len(ground_truth))
    lock_gt = np.ones((len(tiou_thresholds),len(ground_truth))) * -1

    # Sort predictions by decreasing score order.
    sort_idx = prediction['score'].values.argsort()[::-1]
    prediction = prediction.loc[sort_idx].reset_index(drop=True)

    # Initialize true positive and false positive vectors.
    tp = np.zeros((len(tiou_thresholds), len(prediction)))
    fp = np.zeros((len(tiou_thresholds), len(prediction)))
    timediff = np.zeros((len(tiou_thresholds), len(prediction)))

    # Adaptation to query faster
    ground_truth_gbvn = ground_truth.groupby('video-id')

    # Assigning true positive to truly grount truth instances.
    for idx, this_pred in prediction.iterrows():
        try:
            # Check if there is at least one ground truth in the video associated.
            ground_truth_videoid = ground_truth_gbvn.get_group(this_pred['video-id'])
        except Exception as e:
            fp[:, idx] = 1
            continue

        this_gt = ground_truth_videoid.reset_index()
        tiou_arr = segment_iou(this_pred[['t-start', 't-end']].values,
                               this_gt[['t-start', 't-end']].values)
        
        # FIX: Handle NaN or invalid values in gentime
        gentime_pred = this_pred['gentime']
        if pd.isna(gentime_pred) or np.isinf(gentime_pred):
            gentime_pred = 0.0
            
        gentime_gt_arr = this_gt['t-end'].values
        
        # FIX: Handle NaN or invalid values in ground truth times
        gentime_gt_arr = np.nan_to_num(gentime_gt_arr, nan=0.0, posinf=0.0, neginf=0.0)
        
        tiou_sorted_idx = tiou_arr.argsort()[::-1]
        for tidx, tiou_thr in enumerate(tiou_thresholds):
            for jdx in tiou_sorted_idx:
                if tiou_arr[jdx] < tiou_thr:
                    fp[tidx, idx] = 1
                    break
                if lock_gt[tidx, this_gt.loc[jdx]['index']] >= 0:
                    continue
                # Assign as true positive after the filters above.
                tp[tidx, idx] = 1
                
                # FIX: Calculate time difference safely
                time_diff = gentime_pred - gentime_gt_arr[jdx]
                # Handle potential NaN or Inf values
                if pd.isna(time_diff) or np.isinf(time_diff):
                    time_diff = 0.0
                timediff[tidx, idx] = time_diff
                
                lock_gt[tidx, this_gt.loc[jdx]['index']] = idx
                break

            if fp[tidx, idx] == 0 and tp[tidx, idx] == 0:
                fp[tidx, idx] = 1

    ap = np.zeros(len(tiou_thresholds))
    tdiff = np.zeros(len(tiou_thresholds))
    cnt_tp = np.zeros(len(tiou_thresholds))

    for tidx in range(len(tiou_thresholds)):
        # Computing prec-rec
        this_tp = np.cumsum(tp[tidx,:]).astype(float)
        this_fp = np.cumsum(fp[tidx,:]).astype(float)

        rec = this_tp / npos
        prec = this_tp / (this_tp + this_fp)
        
        # FIX: Handle edge cases in precision-recall calculation
        prec = np.nan_to_num(prec, nan=0.0, posinf=0.0, neginf=0.0)
        rec = np.nan_to_num(rec, nan=0.0, posinf=0.0, neginf=0.0)
        
        ap[tidx] = interpolated_prec_rec(prec, rec)
        
        # FIX: Handle time difference calculation safely
        this_tdiff = np.cumsum(timediff[tidx,:]).astype(float)
        if len(this_tdiff) == 0 or this_tp[-1] == 0:
            tdiff[tidx] = 0.0
        else:
            tdiff[tidx] = this_tdiff[-1]
            # Handle NaN or Inf values
            if pd.isna(tdiff[tidx]) or np.isinf(tdiff[tidx]):
                tdiff[tidx] = 0.0
                
        cnt_tp[tidx] = this_tp[-1]
    
    return ap, tdiff, cnt_tp