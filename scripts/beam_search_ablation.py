#################################################################################
#
#             Project Title:  Beam Search ablation
#             Date:           2022-04-30
#
#################################################################################


#################################################################################
#   Module Imports
#################################################################################

import os
import sys
import copy
from datetime import datetime

sys.path.insert(1, '/home/showalte/research/prob_seq_queries/')

import numpy as np
import torch
from collections import defaultdict

from seq_queries.model import get_model
from seq_queries.arguments import get_args, print_args
from seq_queries.train import load_checkpoint
from seq_queries.utils import write_pkl
from seq_queries.sample import lm_proposal, uniform_proposal, beam_search_lower_bound, mc_estimate, beam_search_is_hybrid
from seq_queries.experiments import sample_dynamic_target_token, prep_experiment, beam_search_ablation

#################################################################################
#   Function-Class Declaration
#################################################################################

device=3
folders = ["beam_search_ablation"]
datasets = ['shakespeare','moocs']#'shakespeare'
max_num_queries=100
config_path = "config/testing/sample.yaml"
covs =[0.95,0.75,0.5]
lengths_coverage = {
    # Long GT
    "moocs":[(5,15,0.8), for h in covs],
    "amazon":[(5,15,h) for h in covs],
    "apps":[(5,15,h) for h in covs],
    "shakespeare": [(10,20,h) for h in covs],
}

for dataset_name in datasets:
    len_info = lengths_coverage[dataset_name]
    print("====="*10)
    print(f"* Running for dataset {dataset_name}")
    print("====="*10)
    extra_args = {"max_num_queries":max_num_queries}
    prep_dict = prep_experiment(config_path,
                                dataset_name,
                                device=device,
                                extra_args=extra_args)
    prep_dict['args'].text_dict['text'] = None
    args = prep_dict['args']
    val_dl = prep_dict['val_dl']
    model = prep_dict['model']
    text_dict = args.text_dict
    args.text_dict = None
    print_args(vars(args))
    args.text_dict = text_dict
    print("====="*10)

    for folder in folders:
        for hist_len,total_seq_len,coverage in len_info:
            args = copy.deepcopy(prep_dict['args'])
            args.num_mc_samples = 1000 # For reading from hybrid correctly
            args.estimate_type = beam_search_lower_bound
            args.bs_ablation=True
            args.bs_ablation_max_beams=50000
            args.proposal_func = lm_proposal
            args.use_gpt2 = (dataset_name == 'wikitext')
            args.store_intermediate_lbs=False
            args.batch_size =512
            args.min_variance = False
            args.hist_len = hist_len
            args.total_seq_len = total_seq_len
            args.num_beams = float(coverage)

            print("[{}] | Dataset: {} | Sample type: {} | Num Beams: {} | Hist length {} | Total Seq Length {}"\
                  .format(datetime.now(), dataset_name,folder,args.num_beams,args.hist_len,args.total_seq_len))
            estimates = beam_search_ablation(args, val_dl, model)
            os.makedirs(f"data/{folder}/{dataset_name}/val_dl/",exist_ok=True)
            estimates['metadata']['text_dict']['text'] = None
            args.num_beams = float(coverage)

            # for e,d in estimates.items():
            #     if isinstance(d, list):
            #         print(e, len(d))
            #         for item in d:
            #             print(len(item))
            # sys.exit(1)

            write_pkl(estimates,
            f"data/{folder}/{dataset_name}/val_dl/val-dl_{dataset_name}_{folder.replace('_','-')}_" +
            f"{args.hist_len}h_{args.total_seq_len}s_{args.num_mc_samples}mc" +
            f"{'_' + 'model-budget' if False else f'_{args.num_beams}b'}" +
            f"{f'_{max_num_queries}q' if max_num_queries else ''}.pkl")

            print("====="*10)
