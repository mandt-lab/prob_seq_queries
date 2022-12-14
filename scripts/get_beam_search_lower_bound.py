#################################################################################
#
#             Project Title:  Ground Truth Experiments
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

ROOT =os.path.abspath(os.path.join(__file__,"../../"))
sys.path.insert(1,ROOT)

import numpy as np
import torch
from collections import defaultdict

from seq_queries.model import get_model
from seq_queries.arguments import get_args, print_args
from seq_queries.train import load_checkpoint
from seq_queries.utils import write_pkl
from seq_queries.sample import lm_proposal, uniform_proposal, beam_search_lower_bound, mc_estimate, beam_search_is_hybrid
from seq_queries.experiments import sample_dynamic_target_token, prep_experiment

#################################################################################
#   Function-Class Declaration
#################################################################################

device=0
folders = ["beam_search"]
datasets = ['shakespeare','moocs','apps', 'amazon']
num_mc_samples = 10000
sub_estimates = [10,30,50,100,300, 500,1000,3000,5000,10000]
num_beams = 0.8
model_budget = False
max_num_queries=1000
config_path = "config/sample.yaml"
lengths_coverage = {

    # Beam search gt
    "moocs":[(11,15,0.9)],
    "amazon":[(11,15,0.9)],
    "apps":[(11,15,0.9)],
    "shakespeare":[(11,15,0.9)],
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
            args.num_mc_samples = num_mc_samples
            args.estimate_type = beam_search_lower_bound
            args.proposal_func = lm_proposal
            args.use_gpt2 = (dataset_name == 'wikitext')
            args.store_intermediate_lbs=True
            args.min_variance = False
            args.hist_len = hist_len
            args.total_seq_len = total_seq_len
            args.num_beams = coverage

            if model_budget:
                args.model_budget_filepath = (f"{ROOT}" +
                                            f"data/beam_search_is_hybrid/{dataset_name}/val_dl/val-dl_" +
                    f"{dataset_name}_beam-search-is-hybrid_{args.hist_len}h_{args.total_seq_len}s_{args.num_mc_samples}mc" +
                    f"{f'_{max_num_queries}q' if max_num_queries else ''}.pkl")
                try:
                    assert os.path.exists(args.model_budget_filepath),\
                        f"Model budget filepath {args.model_budget_filepath} does not exist"
                    print(args.model_budget_filepath)
                except Exception as e:
                    print(args.model_budget_filepath)
                    print(e)
                    print("====="*10)
                    continue

            print("[{}] | Dataset: {} | Sample type: {} | Num Beams: {} | Hist length {} | Total Seq Length {}"\
                  .format(datetime.now(), dataset_name,folder,args.num_beams,args.hist_len,args.total_seq_len))
            estimates = sample_dynamic_target_token(args, val_dl, model)
            os.makedirs(f"data/{folder}/{dataset_name}/val_dl/",exist_ok=True)
            estimates['metadata']['text_dict']['text'] = None
            args.num_beams = float(coverage)

            # for e,d in estimates.items():
            #     if isinstance(d, (torch.Tensor, torch.LongTensor)):
            #         print(e, d.shape)
            # sys.exit(1)


            write_pkl(estimates,
            f"data/{folder}/{dataset_name}/val_dl/val-dl_{dataset_name}_{folder.replace('_','-')}_" +
            f"{args.hist_len}h_{args.total_seq_len}s_{args.num_mc_samples}mc" +
            f"{'_' + 'model-budget' if model_budget else f'_{args.num_beams}b'}" +
            f"{f'_{max_num_queries}q' if max_num_queries else ''}.pkl")
            print("====="*10)
