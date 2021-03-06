import time
import pathlib
import numpy as np
import json
from .log import logger
# Timing and Performance

import hdbscan
import umap
import umap.plot
import numpy as np
import pandas as pd
from scipy.spatial.distance import cdist

def timing_info(method):
    def wrapper(*args, **kw):
        start_time = time.time()
        result = method(*args, **kw)
        end_time = time.time()
        logger.info(f"timing_info: {method.__name__}"
                    f"@{round((end_time-start_time)*1000,1)} ms")

        return result

    return wrapper

def record_time_interval(section, start_time, line_break=False):
    """Record a time interval since the last timestamp"""
    end_time = time.time()
    delta = end_time - start_time
    if delta < 1:
        delta *= 1000
        units = "ms"
    else:
        units = "s"
    if line_break:
        logger.debug("PROCESS_TIME:{:>36}    {} {}\n".format(section, round(delta, 1), units))
    else:
        logger.debug("PROCESS_TIME:{:>36}    {} {}".format(section, round(delta, 1), units))
    return end_time

def normalize_numpy_dict(d):
    ret = d.copy()
    for k, v in ret.items():
        if isinstance(v, np.generic):
            ret[k] = np.asscalar(v)
    return ret

def save_json(filename, obj, indent=2, sort_keys=True):
    """Dump an object to disk in json format

    filename: pathname
        Filename to dump to
    obj: object
        Object to dump
    indent: integer
        number of characters to indent
    sort_keys: boolean
        Whether to sort keys before writing. Should be True if you ever use revision control
        on the resulting json file.
    """
    with open(filename, 'w') as fw:
        json.dump(obj, fw, indent=indent, sort_keys=sort_keys)

def load_json(filename):
    """Read a json file from disk"""
    with open(filename) as fw:
        obj = json.load(fw)
    return obj

def head_file(filename, n=5):
    """Return the first `n` lines of a file
    """
    with open(filename, 'r') as fd:
        lines = []
        for i, line in enumerate(fd):
            if i > n:
                break
            lines.append(line)
    return "".join(lines)

def list_dir(path, fully_qualified=False, glob_pattern='*'):
    """do an ls on a path

    fully_qualified: boolean (default: False)
        If True, return a list of fully qualified pathlib objects.
        if False, return just the bare filenames
    glob_pattern: glob (default: '*')
        File mattern to match

    Returns
    -------
    A list of names, or fully qualified pathlib objects"""
    if fully_qualified:
        return list(pathlib.Path(path).glob(glob_pattern))

    return [file.name for file in pathlib.Path(path).glob(glob_pattern)]



class RankedPoints:

    def __init__(self, points, clusterer, metric='euclidean', selection_method='centroid'):
        """ Rank points in a cluster based on their distance to the cluster centroid/medoid.

        From https://github.com/gclen/covid19-kaggle/.

        Parameters
        ----------

        points : array of shape (n_samples, n_features), and must be the same data passed into
                 HDBSCAN

        clusterer : Instance of HDBSCAN that has been fit to data

        metric: string or callable, optional (default='euclidean')
            The metric to use when calculating distance between points in a cluster and
            the cluster centroid/medoid. If metric is a string or callable, it must be one of
            the options allowed by scipy.spatial.distance.cdist for its metric parameter.

        selection_method: string, optional (default='centroid')
            Method to use to find the weighted cluster center. Allowed options are 'centroid'
            and 'medoid'.

        """
        self.clusterer = clusterer
        self.metric = metric

        allowed_methods = ['centroid', 'medoid']
        if selection_method not in allowed_methods:
            raise ValueError(f'Selection method must be one of {allowed_methods}')

        if selection_method == 'centroid' and metric != 'euclidean':
            raise ValueError(f'Metric must be euclidian when using selection_method centroid. '
                             f'Current metric is {metric}')

        self.selection_method = selection_method

        self._embedding_cols = [str(i) for i in range(points.shape[1])]
        self.embedding_df = pd.DataFrame(points, columns=self._embedding_cols)
        self.embedding_df['cluster'] = clusterer.labels_

    def calculate_all_distances_to_center(self):
        """For each cluster calculate the distance from each point to the centroid/medoid"""
        all_distances = pd.DataFrame()
        for label in np.unique(self.embedding_df['cluster']):
            distance_df = self.calculate_distances_for_cluster(label)
            all_distances = pd.concat([all_distances, distance_df])

        self.embedding_df = self.embedding_df.merge(all_distances, left_index=True, right_index=True)

    def calculate_distances_for_cluster(self, cluster_id):
        """For a given cluster_id calculate the distance from each point to the centroid/medoid.

        Parameters
        ----------
        cluster_id : int
            The id of the cluster to compute the distances for. If the cluster id is -1 which
            corresponds to the noise point cluster, then this will return a distance of NaN.
        Returns
        -------
        df : A pandas DataFrame containing the distances from each point to the cluster centroid/medoid.
             The index of the dataframe corresponds to the index in the original data.
        """
        cluster_of_interest = self.embedding_df[self.embedding_df['cluster'] == cluster_id].copy()

        if cluster_of_interest.empty:
            raise ValueError(f'Cluster id {cluster_id} not found')

        # Don't calculate distances for the noise cluster
        if cluster_id == -1:
            return pd.DataFrame(np.nan, columns=['dist_to_rep_point'], index=cluster_of_interest.index)

        if self.selection_method == 'centroid':
            rep_point = self.clusterer.weighted_cluster_centroid(cluster_id)
        if self.selection_method == 'medoid':
            rep_point = self.clusterer.weighted_cluster_medoid(cluster_id)

        dists = cdist(rep_point.reshape((1,len(self._embedding_cols))), cluster_of_interest[self._embedding_cols].values, metric=self.metric)
        return pd.DataFrame(dists[0], columns=['dist_to_rep_point'], index=cluster_of_interest.index)

    def rank_cluster_points_by_distance(self, cluster_id):
        """For a given cluster return a pandas dataframe of points ranked
           by distance to the cluster centroid/medoid
        """
        cluster_of_interest = self.embedding_df[self.embedding_df['cluster'] == cluster_id].copy()

        if cluster_of_interest.empty:
            raise ValueError(f'Cluster id {cluster_id} not found')

        if 'dist_to_rep_point' not in self.embedding_df.columns:
            distance_df = self.calculate_distances_for_cluster(cluster_id)
            cluster_of_interest = cluster_of_interest.merge(distance_df, left_index=True, right_index=True)

        cluster_of_interest.sort_values('dist_to_rep_point', inplace=True)
        return cluster_of_interest

    def get_all_cluster_rankings(self):
        """Calculate the rank of each point within a cluster"""
        if 'dist_to_rep_point' not in self.embedding_df.columns:
            self.calculate_all_distances_to_center()

        self.embedding_df['rank_in_cluster'] = self.embedding_df.groupby('cluster')['dist_to_rep_point'].rank(method='min')

    def get_closest_samples_for_cluster(self, cluster_id, n_samples=5):
        """Get the N closest points to the cluster centroid/medoid"""
        return self.rank_cluster_points_by_distance(cluster_id).head(n_samples)

    def get_furthest_samples_for_cluster(self, cluster_id, n_samples=5):
        """Get the N points furthest away from the cluster centroid/medoid"""
        return self.rank_cluster_points_by_distance(cluster_id).tail(n_samples)

def get_support_index(row):
    """
    Helper function to obtain the words that a document (row) is supported on in the vocabulary.

    Parameters
    ----------
    row:
        a row from the document matrix

    Returns
    -------
    array of column indices that the row is supported on
    """
    inds = row.indices
    data = row.data
    order = np.argsort(-data)
    return inds[order]
