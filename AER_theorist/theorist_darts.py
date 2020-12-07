from abc import ABC, abstractmethod
from AER_theorist.theorist import Theorist
from AER_theorist.theorist import Plot_Types
from AER_theorist.darts.model_search import Network
from AER_theorist.darts.architect import Architect
from AER_theorist.darts.genotypes import PRIMITIVES
from torch.autograd import Variable

import torch
import torch.nn as nn
import torch.nn.functional as F
import pandas
import logging
import numpy as np
import AER_theorist.darts.darts_config as darts_cfg
import AER_theorist.darts.utils as utils
import AER_theorist.darts.visualize as viz
import copy
import os
import csv


class Theorist_DARTS(Theorist, ABC):

    simulation_files = 'AER_theorist/darts/*.py'

    criterion = None

    _model_summary_list = list()

    _lr_plot_name = "Learning Rates"

    def __init__(self, study_name):
        super(Theorist_DARTS, self).__init__(study_name)

        self.model_search_epochs = darts_cfg.epochs

    def get_model_search_parameters(self):

        lm_float = lambda x: float(x)
        lm_int = lambda x: int(x)
        lm_bool = lambda x: bool(x)

        # architecture parameters
        self._model_search_parameters["arch weight decay"] = [self.architect.optimizer.param_groups[0]['weight_decay'], True, lm_float]
        self._model_search_parameters["arch lr"] = [self.architect.optimizer.param_groups[0]['lr'], True, lm_float]
        self._model_search_parameters["arch weight decay df"] = [self.architect.network_weight_decay_df, True, lm_float] # requires call to architect._init_decay_weights()
        self._model_search_parameters["arch unrolled"] = [darts_cfg.unrolled, True, lm_bool]

        # network parameters
        self._model_search_parameters["params momentum"] = [self.optimizer.param_groups[0]['momentum'], True, lm_float]
        self._model_search_parameters["params weight decay"] = [self.optimizer.param_groups[0]['weight_decay'], True, lm_float]
        self._model_search_parameters["classifier weight decay"] = [self.model._classifier_weight_decay, True, lm_float]
        self._model_search_parameters["params current lr"] = [self.optimizer.param_groups[0]['lr'], True, lm_float]
        self._model_search_parameters["params min lr"] = [self.scheduler.eta_min, True, lm_float]

        # training protocol parameters
        self._model_search_parameters["training set proportion"] = [darts_cfg.train_portion, False, lm_float]
        self._model_search_parameters["batch size"] = [darts_cfg.batch_size, False, lm_int]

        return self._model_search_parameters

    def assign_model_search_parameters(self):

        # architecture parameters
        self.architect.optimizer.param_groups[0]['weight_decay'] = self._model_search_parameters["arch weight decay"][0]
        self.architect.optimizer.param_groups[0]['lr'] = self._model_search_parameters["arch lr"][0]
        self.architect.network_weight_decay_df = self._model_search_parameters["arch weight decay df"][0]
        self.architect._init_decay_weights()
        darts_cfg.unrolled = self._model_search_parameters["arch unrolled"][0]

        # network parameters
        self.optimizer.param_groups[0]['momentum'] = self._model_search_parameters["params momentum"][0]
        self.optimizer.param_groups[0]['weight_decay'] = self._model_search_parameters["params weight decay"][0]
        self.model._classifier_weight_decay = self._model_search_parameters["classifier weight decay"][0]
        self.optimizer.param_groups[0]['lr'] = self._model_search_parameters["params current lr"][0]
        self.scheduler.eta_min = self._model_search_parameters["params min lr"][0]

    def init_meta_search(self, object_of_study):
        super(Theorist_DARTS, self).init_meta_search(object_of_study)

        # define loss function
        self.criterion = utils.get_loss_function(object_of_study.__get_output_type__())

        # set configuration
        self._cfg = darts_cfg

        # log: gpu device, parameter configuration
        logging.info('gpu device: %d' % darts_cfg.gpu)
        logging.info("configuration = %s", darts_cfg)

        # sets seeds
        np.random.seed(int(darts_cfg.seed))
        torch.manual_seed(int(darts_cfg.seed))

        # set up meta parameters
        self._meta_parameters = list()
        self._meta_parameters_iteration = 0
        for arch_weight_decay_df in darts_cfg.arch_weight_decay_list:
            for num_graph_nodes in darts_cfg.num_node_list:
                for seed in darts_cfg.seed_list:
                    meta_parameters = [arch_weight_decay_df, int(num_graph_nodes), seed]
                    self._meta_parameters.append(meta_parameters)

    def get_meta_parameters(self, iteration = None):
        if iteration is None:
            iteration = self._meta_parameters_iteration

        return self._meta_parameters[iteration]

    def get_next_meta_parameters(self):
        self._meta_parameters_iteration += 1
        return self.get_meta_parameters()

    def commission_meta_search(self, object_of_study):
        raise Exception('Not implemented.')
        pass

    def search_model(self, object_of_study):
        # initialize model search
        self.init_meta_search(object_of_study)

        # perform architecture search for different hyper-parameters
        for meta_params in self._meta_parameters:
            [arch_weight_decay_df, num_graph_nodes, seed] = meta_params
            self.init_model_search(object_of_study)
            for epoch in range(self.model_search_epochs):
                self.run_model_search_epoch(epoch)
            self.log_model_search(object_of_study)
            self._meta_parameters_iteration += 1

        self.get_best_model(object_of_study)


    def get_best_model(self, object_of_study):

        # determine best model
        best_loss = None
        best_model_file = None
        best_arch_file = None
        for summary_file in self._model_summary_list:
            # read CSV
            data = pandas.read_csv(summary_file, header=0)

            log_losses = np.asarray(data[darts_cfg.csv_log_loss])
            log_losses = log_losses.astype(float)
            min_loss_index = np.argmin(log_losses)

            best_local_log_loss = log_losses[min_loss_index]
            if best_loss is None or best_local_log_loss < best_loss:
                best_loss = best_local_log_loss
                best_model_file = data[darts_cfg.csv_model_file_name][min_loss_index]
                best_arch_file = data[darts_cfg.csv_arch_file_name][min_loss_index]
                best_num_graph_nodes = int(data[darts_cfg.csv_num_graph_node][min_loss_index])

        # load winning model
        model_path = os.path.join(self.results_path, best_model_file + ".pt")
        arch_path = os.path.join(self.results_path, best_arch_file + ".pt")
        model = Network(object_of_study.__get_output_dim__(),
                        self.criterion,
                        steps=best_num_graph_nodes,
                        n_input_states=object_of_study.__get_input_dim__())
        utils.load(model, model_path)
        alphas_normal = torch.load(arch_path)
        model.fix_architecture(True, new_weights=alphas_normal)

        # return winning model
        return model

    def init_model_search(self, object_of_study):

        [arch_weight_decay_df, num_graph_nodes, seed] = self.get_meta_parameters()

        # initializes the model given number of channels, output classes and the training criterion
        self.model = Network(object_of_study.__get_output_dim__(), self.criterion, steps=int(num_graph_nodes),
                        n_input_states=object_of_study.__get_input_dim__(),
                        classifier_weight_decay=darts_cfg.classifier_weight_decay)

        # log size of parameter space
        logging.info("param size: %fMB", utils.count_parameters_in_MB(self.model))

        # optimizer is standard stochastic gradient decent with some momentum and weight decay
        self.optimizer = torch.optim.SGD(
            self.model.parameters(),
            darts_cfg.learning_rate,
            momentum=darts_cfg.momentum,
            weight_decay=darts_cfg.weight_decay)

        # determine training set
        train_data = object_of_study
        num_train = len(train_data)  # number of patterns
        indices = list(range(num_train))  # indices of all patterns
        split = int(np.floor(darts_cfg.train_portion * num_train))  # size of training set

        # combine the training set with a sampler, and provides an iterable over the training set
        self.train_queue = torch.utils.data.DataLoader(
            train_data, batch_size=darts_cfg.batch_size,
            sampler=torch.utils.data.sampler.SubsetRandomSampler(indices[:split]),
            pin_memory=True, num_workers=0)

        # combine the validation set with a sampler, and provides an iterable over the validation set
        self.valid_queue = torch.utils.data.DataLoader(
            train_data, batch_size=darts_cfg.batch_size,
            sampler=torch.utils.data.sampler.SubsetRandomSampler(indices[split:num_train]),
            pin_memory=True, num_workers=0)

        # Set the learning rate of each parameter group using a cosine annealing schedule (model optimization)
        self.scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            self.optimizer, float(self.model_search_epochs), eta_min=darts_cfg.learning_rate_min)

        # generate an architecture of the model
        darts_cfg.arch_weight_decay_df = arch_weight_decay_df
        self.architect = Architect(self.model, darts_cfg)

        # plot variables
        self.num_arch_edges = self.model.alphas_normal.data.shape[0]  # number of architecture edges
        self.num_arch_ops = self.model.alphas_normal.data.shape[1]  # number of operations
        self.arch_ops_labels = PRIMITIVES  # operations
        self.train_error_log = np.empty((self.model_search_epochs, 1))  # log training error
        self.valid_error_log = np.empty((self.model_search_epochs, 1))  # log validation error
        self.param_lr_log = np.empty((self.model_search_epochs, 1))  # log model learning rate
        self.arch_lr_log = np.empty((self.model_search_epochs, 1))  # log architecture learning rate
        self.train_error_log[:] = np.nan
        self.valid_error_log[:] = np.nan
        self.param_lr_log[:] = np.nan
        self.arch_lr_log[:] = np.nan
        self.architecture_weights_log = np.empty(
            (self.model_search_epochs, self.num_arch_edges, self.num_arch_ops))  # log architecture weights
        self.architecture_weights_log[:] = np.nan

        graph_filename = utils.create_output_file_name(file_prefix=darts_cfg.graph_filename,
                                                       log_version=self.model_search_id,
                                                       weight_decay=arch_weight_decay_df,
                                                       k=num_graph_nodes,
                                                       seed=seed)
        self.graph_filepath = os.path.join(self.results_path, graph_filename)


    def run_model_search_epoch(self, epoch):
        # get new learning rate
        lr = self.scheduler.get_last_lr()[0]
        # log new learning rate
        logging.info('epoch: %d', epoch)
        logging.info('learning rate: %e', lr)

        # returns the genotype of the model
        genotype = self.model.genotype()
        # logs the genotype of the model
        logging.info('genotype: %s', genotype)

        # prints and log weights of the normal and reduced architecture
        print(F.softmax(self.model.alphas_normal, dim=-1))

        # training (for one epoch)
        train_obj = train(self.train_queue, self.valid_queue, self.model, self.architect, self.criterion, self.optimizer, lr,
                          darts_cfg.arch_updates_per_epoch, darts_cfg.param_updates_per_epoch)
        # log accuracy on training set
        logging.info('training accuracy: %f', train_obj)

        # validation (for current epoch)
        valid_obj = infer(self.valid_queue, self.model, self.criterion)
        # log accuracy on validation set
        logging.info('validation accuracy: %f', valid_obj)

        # moves the annealing scheduler forward to determine new learning rate
        self.scheduler.step()

        self.train_error_log[epoch] = train_obj
        self.valid_error_log[epoch] = valid_obj

    def log_plot_data(self, epoch, object_of_study):
        self.param_lr_log[epoch] = self.optimizer.param_groups[0]['lr']
        self.arch_lr_log[epoch] = self.architect.optimizer.param_groups[0]['lr']

        # get full data set:
        (input, target) = object_of_study.get_dataset()
        self.target_pattern = target.detach().numpy()
        self.prediction_pattern = self.model(input).detach().numpy()

        # log architecture weights
        self.architecture_weights_log[epoch, :, :] = torch.nn.functional.softmax(self.model.alphas_normal, dim=-1).data.numpy()


    def log_model_search(self, object_of_study):

        [arch_weight_decay_df, num_graph_nodes, seed] = self.get_meta_parameters()
        
        # save model plot
        genotype = self.model.genotype()
        viz.plot(genotype.normal, self.graph_filepath, fileFormat='png',
                 input_labels=object_of_study.__get_input_labels__())

        # stores the model and architecture
        model_filename = self.get_model_filename(arch_weight_decay_df, num_graph_nodes, seed)
        arch_filename = self.get_architecture_filename(arch_weight_decay_df, num_graph_nodes, seed)

        model_filepath = os.path.join(self.results_path, model_filename + '.pt')
        arch_filepath = os.path.join(self.results_path, arch_filename + '.pt')

        utils.save(self.model, model_filepath)
        torch.save(self.model.alphas_normal, arch_filepath)

        model_eval_filepath = self.evaluate_architectures(object_of_study, self.train_queue, self.valid_queue, self.model,
                                                          arch_weight_decay_df, num_graph_nodes, seed)
        
        self._model_summary_list.append(model_eval_filepath)

    def plot_model(self, object_of_study):

        # save model plot
        genotype = self.model.genotype()
        viz.plot(genotype.normal, self.graph_filepath, fileFormat='png',
                 input_labels=object_of_study.__get_input_labels__())

        return self.graph_filepath + ".png"

    def get_performance_plots(self, object_of_study):
        super(Theorist_DARTS, self).get_performance_plots(object_of_study)
        self.update_lr_plot()
        self.update_model_fit_plots(object_of_study)
        return self._performance_plots

    def get_supplementary_plots(self, object_of_study):
        self.update_arch_weights_plots()
        return self._supplementary_plots

    def update_lr_plot(self):

        if hasattr(self, 'param_lr_log') is not True:
            return

        # type
        type = Plot_Types.LINE

        # x data
        x_param_lr = np.linspace(1, len(self.param_lr_log), len(self.param_lr_log))
        x_arch_lr = np.linspace(1, len(self.arch_lr_log), len(self.arch_lr_log))
        x = (x_param_lr, x_arch_lr)

        # y data
        y_param_lr = self.param_lr_log[:]
        y_arch_lr = self.arch_lr_log[:]
        y = (y_param_lr, y_arch_lr)

        # axis limits
        x_limit = [1, self.model_search_epochs]

        if np.isnan(self.param_lr_log[:]).all() and np.isnan(self.arch_lr_log[:]).all():
            y_limit = [0, 1]
        else:
            y_max = np.nanmax([np.nanmax(self.param_lr_log[:]), np.nanmax(self.arch_lr_log[:])])
            y_limit = [0, y_max]

        # axis labels
        x_label = "Learning Rate"
        y_label = "Epochs"

        # legend
        legend = ('Parameter LR', 'Architecture LR')

        # generate plot dictionary
        plot_dict = self._generate_line_plot_dict(type, x, y, x_limit, y_limit, x_label, y_label, legend)
        self._performance_plots[self._lr_plot_name] = plot_dict

    def update_loss_plot(self):

        if hasattr(self, 'train_error_log') is not True:
            return

        # type
        type = Plot_Types.LINE

        # x data
        x_train = np.linspace(1, len(self.train_error_log), len(self.train_error_log))
        x_valid = np.linspace(1, len(self.valid_error_log), len(self.valid_error_log))
        x = (x_train, x_valid)

        # y data
        y_train = self.train_error_log[:]
        y_valid = self.valid_error_log[:]
        y = (y_train, y_valid)

        # axis limits
        x_limit = [1, self.model_search_epochs]

        if np.isnan(self.train_error_log[:]).all() and np.isnan(self.valid_error_log[:]).all():
            y_limit = [0, 1]
        else:
            y_max = np.nanmax([np.nanmax(self.train_error_log[:]), np.nanmax(self.valid_error_log[:])])
            y_limit = [0, y_max]

        # axis labels
        add_str = ""
        if isinstance(self.criterion , nn.MSELoss):
            add_str = " (MSE)"
        elif isinstance(self.criterion , nn.cross_entropy) or isinstance(self.criterion , nn.nn.CrossEntropyLoss):
            add_str = " (Cross-Entropy)"
        x_label = "Loss" + add_str
        y_label = "Epochs"

        # legend
        legend = ('Training Loss', 'Validation Loss')

        # generate plot dictionary
        plot_dict = self._generate_line_plot_dict(type, x, y, x_limit, y_limit, x_label, y_label, legend)
        self._performance_plots[self._loss_plot_name] = plot_dict

    def update_model_fit_plots(self, object_of_study):

        if hasattr(self, 'model') is not True:
            return

        # get all possible plots
        (IV_list_1, IV_list_2, DV_list) = self.get_model_fit_plot_list(object_of_study)

        # for each plot
        for IV1, IV2, DV in zip(IV_list_1, IV_list_2, DV_list):

            IVs = [IV1, IV2]

            # generate model prediction
            resolution = 100
            counterbalanced_input = object_of_study.get_counterbalanced_input(resolution)
            if IV2 is None:  # prepare line plot
                x_prediction = object_of_study.get_IVs_from_input(counterbalanced_input, IV1)
            else:
                x_prediction = (object_of_study.get_IVs_from_input(counterbalanced_input, IV1), object_of_study.get_IVs_from_input(counterbalanced_input, IV2))
            y_prediction = self.model(counterbalanced_input)

            # get data points
            (input, output)  = object_of_study.get_dataset()
            if IV2 is None:  # prepare line plot
                x_data = object_of_study.get_IVs_from_input(input, IV1)
            else:
                x_data = (object_of_study.get_IVs_from_input(input, IV1), object_of_study.get_IVs_from_input(input, IV2))
            y_data = object_of_study.get_DV_from_output(output, DV)

            # determine y limits
            y_limit = [np.amin(y_data.numpy()), np.amax(y_data.numpy())]

            # determine y_label
            y_label = DV.get_variable_label()

            # determine legend:
            legend = ('Data', 'Prediction')

            # select data based on whether this is a line or a surface plot
            if IV2 is None: # prepare line plot

                # determine plot type
                type = Plot_Types.LINE_SCATTER
                plot_name = DV.get_name() + "(" + IV1.get_name() + ")"

                # determine x limits
                x_limit = object_of_study.get_variable_limits(IV1)

                # determine x_label
                x_label = IV1.get_variable_label()

            else: # prepare surface plot
                # determine plot type
                type = Plot_Types.SURFACE_SCATTER

                # determine x limits
                x_limit = (object_of_study.get_variable_limits(IV1),
                           object_of_study.get_variable_limits(IV2))

                # determine x_labels
                x_label = (IV1.get_variable_label(), IV2.get_variable_label())

            plot_dict = self._generate_line_plot_dict(type, x=x_data.detach().numpy(), y=y_data.detach().numpy(), x_limit=x_limit, y_limit=y_limit, x_label=x_label, y_label=y_label,
                                     legend=legend, image=None, x_model=x_prediction.detach().numpy(), y_model=y_prediction.detach().numpy(), x_highlighted=None,
                                     y_highlighted=None)
            self._performance_plots[plot_name] = plot_dict

    def get_model_fit_plot_list(self, object_of_study):
        IV_list_1 = list()
        IV_list_2 = list()
        DV_list = list()

        # combine each IV with each IV with each DV
        independent_variables_1 = object_of_study.independent_variables + object_of_study.covariates
        independent_variables_2 = [None] + object_of_study.independent_variables + object_of_study.covariates

        for IV1 in independent_variables_1:
            for IV2 in independent_variables_2:
                for DV in object_of_study.dependent_variables:
                    if IV1 != IV2:
                        IV_list_1.append(IV1)
                        IV_list_2.append(IV2)
                        DV_list.append(DV)

        # combine each IV
        return (IV_list_1, IV_list_2, DV_list)


    def update_arch_weights_plots(self):

        if hasattr(self, 'architecture_weights_log') is False:
            return
        # type
        type = Plot_Types.LINE

        # x axis label
        x_label = "Epoch"

        # x axis limits
        x_limit = [1, self.architecture_weights_log.shape[0]]

        for edge in range(self.num_arch_edges):

        #     self.num_arch_edges = model.alphas_normal.data.shape[0]  # number of architecture edges
        #     self.num_arch_ops = model.alphas_normal.data.shape[1]  # number of operations
        #     self.arch_ops_labels = PRIMITIVES  # operations
        #     self.train_error_log = np.empty((self.model_search_epochs, 1))  # log training error
        #     self.valid_error_log = np.empty((self.model_search_epochs, 1))  # log validation error
        #     self.train_error_log[:] = np.nan
        #     self.valid_error_log[:] = np.nan
        #     self.architecture_weights_log = np.empty(
        #         (self.model_search_epochs, self.num_arch_edges, self.num_arch_ops))  # log architecture weights

            # plot name
            plot_name = "Edge " + str(edge)

            # y axis label
            y_label = "Edge Weight (" + str(edge) + ")"

            # x data
            x = list()

            # y data
            y = list()
            legend = list()

            # add line (y-data and legend) for each primitive
            for op_idx, operation in enumerate(self.arch_ops_labels):

                x.append(np.linspace(1, self.architecture_weights_log.shape[0], self.architecture_weights_log.shape[0]))
                y.append(self.architecture_weights_log[:, edge, op_idx])
                legend.append(operation)

            # y axis limits
            if np.isnan(self.architecture_weights_log[:, edge, :]).all():
                y_limit = [0, 1]
            else:
                y_limit = [np.nanmin(np.nanmin(self.architecture_weights_log[:, edge, :])),
                           np.nanmax(np.nanmax(self.architecture_weights_log[:, edge, :]))]
            if y_limit[0] == y_limit[1]:
                y_limit = [0, 1]

            # generate plot dictionary
            plot_dict = self._generate_line_plot_dict(type, x, y, x_limit, y_limit, x_label, y_label, legend)
            self._supplementary_plots[plot_name] = plot_dict

        return self._supplementary_plots

    def _generate_line_plot_dict(self, type, x, y, x_limit=None, y_limit=None, x_label=None, y_label=None, legend=None, image=None, x_model=None, y_model=None, x_highlighted=None, y_highlighted=None):
        # generate plot dictionary
        plot_dict = dict()
        plot_dict[self.plot_key_type] = type
        plot_dict[self.plot_key_x_data] = x
        plot_dict[self.plot_key_y_data] = y
        if x_limit is not None:
            plot_dict[self.plot_key_x_limit] = x_limit
        if y_limit is not None:
            plot_dict[self.plot_key_y_limit] = y_limit
        if x_label is not None:
            plot_dict[self.plot_key_x_label] = x_label
        if y_label is not None:
            plot_dict[self.plot_key_y_label] = y_label
        if legend is not None:
            plot_dict[self.plot_key_legend] = legend
        if image is not None:
            plot_dict[self.plot_key_image] = image
        if x_model is not None:
            plot_dict[self.plot_key_x_model] = x_model
        if y_model is not None:
            plot_dict[self.plot_key_y_model] = y_model
        if x_highlighted is not None:
            plot_dict[self.plot_key_x_highlighted_data] = x_highlighted
        if y_highlighted is not None:
            plot_dict[self.plot_key_y_highlighted_data] = y_highlighted

        return plot_dict

    def update_pattern_plot(self):

        # type
        type = Plot_Types.IMAGE

        target = self.target_pattern
        prediction = self.prediction_pattern

        if len(target) == 0:
            self._performance_plots[self._pattern_plot_name] = None
            return

        im = np.concatenate((target, prediction), axis=1)

        # seperator
        x = np.ones(target.shape[0]) * (target.shape[1] - 0.5)
        y = np.linspace(1, target.shape[0], target.shape[0])

        # axis labels
        x_label = "Output"
        y_label = "Pattern"

        # generate plot dictionary
        plot_dict = self._generate_line_plot_dict(type, x, y, x_label=x_label, y_label=y_label, image=im)
        self._performance_plots[self._pattern_plot_name] = plot_dict


    def get_model_filename(self, arch_weight_decay_df, num_graph_nodes, seed):
        filename = utils.create_output_file_name(file_prefix='model_weights',
                                                         log_version=self.model_search_id,
                                                         weight_decay=arch_weight_decay_df,
                                                         k=num_graph_nodes,
                                                         seed=seed)
        return filename

    def get_architecture_filename(self, arch_weight_decay_df, num_graph_nodes, seed):
        filename = utils.create_output_file_name(file_prefix='architecture_weights',
                                                         log_version=self.model_search_id,
                                                         weight_decay=arch_weight_decay_df,
                                                         k=num_graph_nodes,
                                                         seed=seed)
        return filename

    def evaluate_architectures(self, object_of_study, train_queue, valid_queue, model, arch_weight_decay_df, num_graph_nodes, seed):

      criterion = self.criterion
      criterion_loss_log = list()
      model_name_log = list()
      arch_name_log = list()
      num_graph_node_log = list()

      # generate general model file name
      model_filename_gen = self.get_model_filename(arch_weight_decay_df, num_graph_nodes, seed)

      arch_filename_gen = self.get_architecture_filename(arch_weight_decay_df, num_graph_nodes, seed)


      # subsample models and retrain
      sampled_weights = list()
      for sample_id in range(darts_cfg.n_models_sampled):

          logging.info('architecture evaluation for sampled model: %d / %d', sample_id+1, darts_cfg.n_models_sampled)

          # sample architecture weights
          found_weights = False
          if(sample_id == 0):
              candidate_weights = model.max_alphas_normal()
              found_weights = True
          else:
              candidate_weights = model.sample_alphas_normal(darts_cfg.sample_amp)

          while found_weights is False:
                weights_are_novel = True
                for logged_weights in sampled_weights:
                    if torch.eq(logged_weights, candidate_weights).all() is True:
                        weights_are_novel = False
                if weights_are_novel:
                    novel_weights = candidate_weights
                    found_weights = True
                else:
                    candidate_weights = model.sample_alphas_normal()

          # store sampled architecture weights
          sampled_weights.append(candidate_weights)

          # reinitialize weights if desired
          if darts_cfg.reinitialize_weights:
              new_model = Network(object_of_study.__get_output_dim__(), criterion, steps=int(num_graph_nodes), n_input_states=object_of_study.__get_input_dim__(), classifier_weight_decay=darts_cfg.classifier_weight_decay)
          else:
              new_model = copy.deepcopy(model)

          new_model.fix_architecture(True, candidate_weights)

          # optimizer is standard stochastic gradient decent with some momentum and weight decay
          optimizer = torch.optim.SGD(
              new_model.parameters(),
              darts_cfg.learning_rate,
              momentum=darts_cfg.momentum,
              weight_decay=darts_cfg.weight_decay)

          # Set the learning rate of each parameter group using a cosine annealing schedule (model optimization)
          scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
              optimizer, float(self.model_search_epochs), eta_min=darts_cfg.learning_rate_min)

          # train model
          for epoch in range(self.model_search_epochs):

              # get new learning rate
              lr = scheduler.get_last_lr()[0]

              new_model.train()  # Sets the module in training mode

              for param_step in range(darts_cfg.param_updates_per_epoch):
                  # get input and target
                  input_search, target_search = next(iter(train_queue))
                  input = Variable(input_search, requires_grad=False)  # .cuda()
                  target = Variable(target_search, requires_grad=False)  # .cuda(async=True)

                  input, target = format_input_target(input, target, criterion)

                  # zero out gradients
                  optimizer.zero_grad()
                  # compute loss for the model
                  logits = new_model(input)
                  loss = criterion(logits, target)
                  # update gradients for model
                  loss.backward()
                  # clips the gradient norm
                  nn.utils.clip_grad_norm_(new_model.parameters(), darts_cfg.grad_clip)
                  # moves optimizer one step (applies gradients to weights)
                  optimizer.step()
                  # applies weight decay to classifier weights
                  model.apply_weight_decay_to_classifier(lr)

              # if in debug mode, print loss during architecture evaluation
              logging.info('epoch %d', epoch)
              logging.info('criterion loss %f', loss)

              # moves the annealing scheduler forward to determine new learning rate
              scheduler.step()

          # evaluate model
          criterion_loss = infer(valid_queue, new_model, criterion, silent = True)
          criterion_loss_log.append(criterion_loss.numpy())

          # get model name
          model_filename = model_filename_gen + '_sample' + str(sample_id)
          arch_filename = arch_filename_gen + '_sample' + str(sample_id)
          model_filepath = os.path.join(self.results_path, model_filename + '.pt')
          arch_filepath = os.path.join(self.results_path, arch_filename + '.pt')
          model_graph_filepath = os.path.join(self.results_path, model_filename)
          model_name_log.append(model_filename)
          arch_name_log.append(arch_filename)
          num_graph_node_log.append(num_graph_nodes)
          genotype = new_model.genotype()

          # save model
          utils.save(new_model, model_filepath)
          torch.save(new_model.alphas_normal, arch_filepath)
          print('Saving model weights: ' + model_filepath)
          viz.plot(genotype.normal, model_graph_filepath, viewFile=False, input_labels=object_of_study.__get_input_labels__())
          print('Saving model graph: ' + model_graph_filepath)
          print('Saving architecture weights: ' + arch_filepath)

      # get name for csv log file
      model_filename_csv = model_filename_gen + '.csv'
      model_filepath = os.path.join(self.results_path, model_filename_csv)

      # save csv file
      rows = zip(model_name_log, arch_name_log, num_graph_node_log, criterion_loss_log)
      with open(model_filepath, "w") as f:
          writer = csv.writer(f)
          writer.writerow([darts_cfg.csv_model_file_name, darts_cfg.csv_arch_file_name, darts_cfg.csv_num_graph_node, darts_cfg.csv_log_loss])
          for row in rows:
              writer.writerow(row)

      return model_filepath


def format_input_target(input, target, criterion):

    if isinstance(criterion, nn.CrossEntropyLoss):
        target = target.squeeze()

    return (input, target)


# trains model for one architecture epoch
def train(train_queue, valid_queue, model, architect, criterion, optimizer, lr, arch_updates_per_epoch=1, param_updates_per_epoch = 1):
  objs = utils.AvgrageMeter() # metric that averages

  objs_log = torch.zeros(arch_updates_per_epoch)

  for arch_step in range(arch_updates_per_epoch):
    # for step, (input, target) in enumerate(train_queue): # for every pattern

    model.train() # Sets the module in training mode
    logging.info("architecture step: %d", arch_step)

    # get a random minibatch from the search queue with replacement
    input_search, target_search = next(iter(valid_queue))
    input_search = Variable(input_search, requires_grad=False) #.cuda()
    target_search = Variable(target_search, requires_grad=False) #.cuda(async=True)

    input_search, target_search = format_input_target(input_search, target_search, criterion)

    # FIRST STEP: UPDATE ARCHITECTURE (ALPHA)
    architect.step(input_search, target_search, lr, optimizer, unrolled=darts_cfg.unrolled)

    # SECOND STEP: UPDATE MODEL PARAMETERS (W)
    for param_step in range(param_updates_per_epoch):

      # get input and target
      input_search, target_search = next(iter(train_queue))
      input = Variable(input_search, requires_grad=False)  # .cuda()
      target = Variable(target_search, requires_grad=False)  # .cuda(async=True)

      input, target = format_input_target(input, target, criterion)

      # zero out gradients
      optimizer.zero_grad()
      # compute loss for the model
      logits = model(input)
      loss = criterion(logits, target)
      # update gradients for model
      loss.backward()
      # clips the gradient norm
      nn.utils.clip_grad_norm_(model.parameters(), darts_cfg.grad_clip)
      # moves optimizer one step (applies gradients to weights)
      optimizer.step()
      # applies weight decay to classifier weights
      model.apply_weight_decay_to_classifier(lr)

      # compute accuracy metrics
      n = input.size(0)
      objs.update(loss.data, n)

    objs_log[arch_step] = objs.avg

    if arch_step % darts_cfg.report_freq == 0:
      logging.info("architecture step (loss): %03d (%e)", arch_step, objs.avg)

  return objs.avg


# computes accuracy for validation set
def infer(valid_queue, model, criterion, silent = False):
  objs = utils.AvgrageMeter()
  model.eval()

  for step, (input, target) in enumerate(valid_queue):
    input = Variable(input, requires_grad=True) #.cuda()
    target = Variable(target, requires_grad=True) #.cuda(async=True)

    input, target = format_input_target(input, target, criterion)

    logits = model(input)
    loss = criterion(logits, target)

    n = input.size(0)
    objs.update(loss.data, n)

    if silent is False:
        if step % darts_cfg.report_freq == 0:
          logging.info('architecture step (accuracy): %03d (%e)', step, objs.avg)

  return objs.avg


# def architecture_search(self, object_of_study, arch_weight_decay_df, num_graph_nodes, seed):
#     # initializes the model given number of channels, output classes and the training criterion
#     model = Network(object_of_study.__get_output_dim__(), self.criterion, steps=int(num_graph_nodes),
#                     n_input_states=object_of_study.__get_input_dim__(),
#                     classifier_weight_decay=darts_cfg.classifier_weight_decay)
#
#     # log size of parameter space
#     logging.info("param size: %fMB", utils.count_parameters_in_MB(model))
#
#     # optimizer is standard stochastic gradient decent with some momentum and weight decay
#     optimizer = torch.optim.SGD(
#         model.parameters(),
#         darts_cfg.learning_rate,
#         momentum=darts_cfg.momentum,
#         weight_decay=darts_cfg.weight_decay)
#
#     # determine training set
#     train_data = object_of_study
#     num_train = len(train_data)  # number of patterns
#     indices = list(range(num_train))  # indices of all patterns
#     split = int(np.floor(darts_cfg.train_portion * num_train))  # size of training set
#
#     # combine the training set with a sampler, and provides an iterable over the training set
#     train_queue = torch.utils.data.DataLoader(
#         train_data, batch_size=darts_cfg.batch_size,
#         sampler=torch.utils.data.sampler.SubsetRandomSampler(indices[:split]),
#         pin_memory=True, num_workers=0)
#
#     # combine the validation set with a sampler, and provides an iterable over the validation set
#     valid_queue = torch.utils.data.DataLoader(
#         train_data, batch_size=darts_cfg.batch_size,
#         sampler=torch.utils.data.sampler.SubsetRandomSampler(indices[split:num_train]),
#         pin_memory=True, num_workers=0)
#
#     # Set the learning rate of each parameter group using a cosine annealing schedule (model optimization)
#     scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
#         optimizer, float(self.model_search_epochs), eta_min=darts_cfg.learning_rate_min)
#
#     # generate an architecture of the model
#     architect = Architect(model, darts_cfg)
#
#     # plot variables
#     self.num_arch_edges = model.alphas_normal.data.shape[0]  # number of architecture edges
#     self.num_arch_ops = model.alphas_normal.data.shape[1]  # number of operations
#     self.arch_ops_labels = PRIMITIVES  # operations
#     self.train_error_log = np.empty((self.model_search_epochs, 1))  # log training error
#     self.valid_error_log = np.empty((self.model_search_epochs, 1))  # log validation error
#     self.train_error_log[:] = np.nan
#     self.valid_error_log[:] = np.nan
#     self.architecture_weights_log = np.empty(
#         (self.model_search_epochs, self.num_arch_edges, self.num_arch_ops))  # log architecture weights
#
#     graph_filename = utils.create_output_file_name(file_prefix=darts_cfg.graph_filename,
#                                                    log_version=self.model_search_id,
#                                                    weight_decay=arch_weight_decay_df,
#                                                    k=num_graph_nodes,
#                                                    seed=seed)
#     graph_filepath = os.path.join(self.results_path, graph_filename)
#
#     # architecture search loop
#     for epoch in range(self.model_search_epochs):
#         # get new learning rate
#         lr = scheduler.get_last_lr()[0]
#         # log new learning rate
#         logging.info('epoch: %d', epoch)
#         logging.info('learning rate: %e', lr)
#
#         # returns the genotype of the model
#         genotype = model.genotype()
#         # logs the genotype of the model
#         logging.info('genotype: %s', genotype)
#
#         # prints and log weights of the normal and reduced architecture
#         print(F.softmax(model.alphas_normal, dim=-1))
#
#         # training (for one epoch)
#         train_obj = train(train_queue, valid_queue, model, architect, self.criterion, optimizer, lr,
#                           darts_cfg.arch_updates_per_epoch, darts_cfg.param_updates_per_epoch)
#         # log accuracy on training set
#         logging.info('training accuracy: %f', train_obj)
#
#         # validation (for current epoch)
#         valid_obj = infer(valid_queue, model, self.criterion)
#         # log accuracy on validation set
#         logging.info('validation accuracy: %f', valid_obj)
#
#         # moves the annealing scheduler forward to determine new learning rate
#         scheduler.step()
#
#         # log data
#         self.architecture_weights_log[epoch, :, :] = torch.nn.functional.softmax(model.alphas_normal,
#                                                                                  dim=-1).data.numpy()
#         self.train_error_log[epoch] = train_obj
#         self.valid_error_log[epoch] = valid_obj
#
#     # save model plot
#     genotype = model.genotype()
#     viz.plot(genotype.normal, graph_filepath, fileFormat='png',
#              input_labels=object_of_study.__get_input_labels__())
#
#     # stores the model and architecture
#     model_filename = self.get_model_filename(arch_weight_decay_df, num_graph_nodes, seed)
#     arch_filename = self.get_architecture_filename(arch_weight_decay_df, num_graph_nodes, seed)
#
#     model_filepath = os.path.join(self.results_path, model_filename + '.pt')
#     arch_filepath = os.path.join(self.results_path, arch_filename + '.pt')
#
#     utils.save(model, model_filepath)
#     torch.save(model.alphas_normal, arch_filepath)
#
#     model_eval_filepath = self.evaluate_architectures(object_of_study, train_queue, valid_queue, model,
#                                                       arch_weight_decay_df, num_graph_nodes, seed)
#
#     return model_eval_filepath