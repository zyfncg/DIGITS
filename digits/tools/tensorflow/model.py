# Copyright (c) 2016, NVIDIA CORPORATION.  All rights reserved.
#
# This document should comply with PEP-8 Style Guide
# Linter: pylint

"""
Interface for setting up and creating a model in Tensorflow.

"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import logging
import tensorflow as tf
import horovod.tensorflow as hvd
from tensorflow.python.framework import ops

# Local imports
import tf_data
import utils as digits
from utils import model_property

logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.INFO)

# Constants
SUMMARIZE_TOWER_STATS = False


class Model(object):
    """
    Wrapper around the actual tensorflow workflow process.
    This is structured in a way that the user should only care about
    creating the model while using the DIGITS UI to select the
    optimizer and other options.

    This class is executed to start a tensorflow workflow.
    """

    def __init__(self, stage, croplen, nclasses, optimization=None, momentum=None, reuse_variable=False):
        self.stage = stage
        self.croplen = croplen
        self.nclasses = nclasses
        self.dataloader = None
        self.queue_coord = None
        self.queue_threads = None

        self._optimization = optimization
        self._momentum = momentum
        self.summaries = []
        self.towers = []
        self._train = None
        self._reuse = reuse_variable

        # Touch to initialize
        # if optimization:
        #     self.learning_rate
        #     self.global_step
        #     self.optimizer

    def create_dataloader(self, db_path):
        self.dataloader = tf_data.LoaderFactory.set_source(db_path, is_inference=(self.stage == digits.STAGE_INF))
        # @TODO(tzaman) communicate the dataloader summaries to our Model summary list
        self.dataloader.stage = self.stage
        self.dataloader.croplen = self.croplen
        self.dataloader.nclasses = self.nclasses

    def init_dataloader(self):
        with tf.device('/cpu:0'):
            with tf.name_scope(digits.GraphKeys.LOADER):
                self.dataloader.create_input_pipeline()

    def create_model(self, obj_UserModel, stage_scope, batch_x=None):

        if batch_x is None:
            self.init_dataloader()
            batch_x = self.dataloader.batch_x
            if self.stage != digits.STAGE_INF:
                batch_y = self.dataloader.batch_y
        else:
            assert self.stage == digits.STAGE_INF
            batch_x = batch_x

        batch_x_split = [batch_x]
        if self.stage != digits.STAGE_INF:  # Has no labels
            batch_y_split = [batch_y]

        if self.stage != digits.STAGE_INF:
            tower_model = self.add_tower(obj_tower=obj_UserModel,
                                         x=batch_x_split[0],
                                         y=batch_y_split[0])
        else:
            tower_model = self.add_tower(obj_tower=obj_UserModel,
                                         x=batch_x_split[0],
                                         y=None)
        with tf.variable_scope(digits.GraphKeys.MODEL, reuse=self._reuse):

            tower_model.inference

            # Reuse the variables in this scope for the next tower/device
            tf.get_variable_scope().reuse_variables()

            if self.stage != digits.STAGE_INF:
                with tf.name_scope(digits.GraphKeys.LOSS):
                    loss = tower_model.loss
                    tf.add_to_collection(digits.GraphKeys.LOSSES, loss)

                    self.summaries.append(tf.summary.scalar('loss', loss))

        if self.stage == digits.STAGE_TRAIN:
            opt = hvd.DistributedOptimizer(self.optimizer)
            self._train = opt.minimize(loss, global_step=self.global_step)

    def start_queue_runners(self, sess):
        logging.info('Starting queue runners (%s)', self.stage)
        # Distinguish the queue runner collection (for easily obtaining them by collection key)
        queue_runners = tf.get_collection(tf.GraphKeys.QUEUE_RUNNERS, scope=self.stage + '.*')
        for qr in queue_runners:
            if self.stage in qr.name:
                tf.add_to_collection(digits.GraphKeys.QUEUE_RUNNERS, qr)

        self.queue_coord = tf.train.Coordinator()
        self.queue_threads = tf.train.start_queue_runners(sess=sess, coord=self.queue_coord,
                                                          collection=digits.GraphKeys.QUEUE_RUNNERS)
        logging.info('Queue runners started (%s)', self.stage)

    def __del__(self):
        # Destructor
        if self.queue_coord:
            # Close and terminate the queues
            self.queue_coord.request_stop()
            self.queue_coord.join(self.queue_threads)

    def add_tower(self, obj_tower, x, y):
        is_training = self.stage == digits.STAGE_TRAIN
        is_inference = self.stage == digits.STAGE_INF
        input_shape = self.dataloader.get_shape()
        tower = obj_tower(x, y, input_shape, self.nclasses, is_training, is_inference)
        self.towers.append(tower)
        return tower

    @model_property
    def train(self):
        return self._train

    @model_property
    def summary(self):
        """
        Merge train summaries
        """
        for t in self.towers:
            self.summaries += t.summaries

        if not len(self.summaries):
            logging.error("No summaries defined. Please define at least one summary.")
            exit(-1)
        return tf.summary.merge(self.summaries)

    @model_property
    def global_step(self):
        # Force global_step on the CPU, becaues the GPU's first step will end at 0 instead of 1.
        with tf.device('/cpu:0'):
            return tf.get_variable('global_step', [], initializer=tf.constant_initializer(0),
                                   trainable=False)

    @model_property
    def learning_rate(self):
        # @TODO(tzaman): the learning rate is a function of the global step, so we could
        #  define it entirely in tf ops, instead of a placeholder and feeding.
        with tf.device('/cpu:0'):
            lr = tf.placeholder(tf.float32, shape=[], name='learning_rate')
            self.summaries.append(tf.summary.scalar('lr', lr))
            return lr

    @model_property
    def optimizer(self):
        logging.info("Optimizer:%s", self._optimization)
        if self._optimization == 'sgd':
            return tf.train.GradientDescentOptimizer(learning_rate=self.learning_rate)
        elif self._optimization == 'adadelta':
            return tf.train.AdadeltaOptimizer(learning_rate=self.learning_rate)
        elif self._optimization == 'adagrad':
            return tf.train.AdagradOptimizer(learning_rate=self.learning_rate)
        elif self._optimization == 'adagradda':
            return tf.train.AdagradDAOptimizer(learning_rate=self.learning_rate,
                                               global_step=self.global_step)
        elif self._optimization == 'momentum':
            return tf.train.MomentumOptimizer(learning_rate=self.learning_rate,
                                              momentum=self._momentum)
        elif self._optimization == 'adam':
            return tf.train.AdamOptimizer(learning_rate=self.learning_rate)
        elif self._optimization == 'ftrl':
            return tf.train.FtrlOptimizer(learning_rate=self.learning_rate)
        elif self._optimization == 'rmsprop':
            return tf.train.RMSPropOptimizer(learning_rate=self.learning_rate,
                                             momentum=self._momentum)
        else:
            logging.error("Invalid optimization flag %s", self._optimization)
            exit(-1)


class Tower(object):

    def __init__(self, x, y, input_shape, nclasses, is_training, is_inference):
        self.input_shape = input_shape
        self.nclasses = nclasses
        self.is_training = is_training
        self.is_inference = is_inference
        self.summaries = []
        self.x = x
        self.y = y
        self.train = None

    def gradientUpdate(self, grad):
        return grad
