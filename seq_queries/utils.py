import torch
import time
import torch.nn as nn
import numpy as np
import random
import math
import yaml
import pickle as pkl
import json
import ast

from datetime import datetime
import torch.nn.functional as F

#######################################################################
# Data
#######################################################################

def _hidden_state_select(state,i,
                         is_root = False,
                         uses_attention=False):
    if uses_attention:
        state = tuple(
            # (layers, (2, (samples, num_heads, seq_len, dim)))
            [(h1[i].unsqueeze(0), h2[i].unsqueeze(0))
                for (h1,h2) in state])
        if not is_root:
            state = tuple(
                [(h1[...,-1,:].unsqueeze(-2),
                  h2[...,-1,:].unsqueeze(-2))
                 for (h1,h2) in state])
    else:
        if isinstance(state,tuple):
            state = tuple([s[...,i,:] for s in state])
        else:
            state = state[...,i,:]
    return state

def _tup_cpu(tup, force=False):
    if isinstance(tup,tuple) and isinstance(tup[0],tuple):
        return _tup_cpu_gpt2(tup)
    elif force or isinstance(tup, tuple):
        return tuple([t.cpu() for t in tup])
    elif tup: return tup.cpu()
    else: return tup


def _tup_cpu_gpt2(tup, force=False):
    if not tup: return tup
    elif force or isinstance(tup, tuple):
        return tuple([(t1.cpu(),t2.cpu()) for (t1,t2) in tup])
    else: return tup.cpu()

def _tup_gpu_gpt2(tup, device,force=False):
    if not tup: return tup
    elif force or isinstance(tup, tuple):
        return tuple([(t1.to(device),t2.to(device)) for (t1,t2) in tup])
    else: return tup.to(device)

def _tup_gpu(tup, device,force=False):
    if isinstance(tup,tuple) and isinstance(tup[0],tuple):
        return _tup_gpu_gpt2(tup,device)
    if not tup: return tup
    if force or isinstance(tup, tuple):
        return tuple([t.to(device) for t in tup])
    return tup.to(device)

def read_yaml(filename):
    if filename is None: return None
    with open(filename,'r') as file:
        return yaml.safe_load(file)

def write_yaml(data,filename):
    with open(filename,'w') as file:
        return yaml.dump(data,file)

def write_pkl(data,name):
    with open(f'{name}','wb') as file:
        data = pkl.dump(data,file)

def read_pkl(name):
    with open(f'{name}','rb') as file:
        data = pkl.load(file)
    return data


def read_json(filepath):
    return json.load(open(filepath,'r'))

def write_json(data,filepath):
    with open(filepath,'w') as file:
        json.dump(data,file)


#######################################################################
# Model evaluation metrics
#######################################################################

def accuracy_score(gt, logits):
    preds = torch.argmax(F.softmax(logits,dim=-1),dim=-1).flatten()
    return ((preds == gt).sum()/preds.shape[0])*100


#######################################################################
# Classes
#######################################################################


class Log(nn.Module):

    def __init__(self):
        super().__init__()

    def forward(self, x):
        return torch.log(x)

class Identity(nn.Module):

    def __init__(self):
        super().__init__()

    def forward(self, x):
        return x

class GELU(nn.Module):

    def __init__(self):
        super().__init__()

    def forward(self, x):
        return 0.5 * x * (1 + torch.tanh(math.sqrt(2 / math.pi) * (x + 0.044715 * torch.pow(x, 3))))


ACTIVATIONS = {
    'relu': nn.ReLU,
    'sigmoid': nn.Sigmoid,
    'tanh': nn.Tanh,
    'log': Log,
    'identity': Identity,
    'gelu': GELU,
    'softplus': nn.Softplus,
}


def print_log(*args):
    """Custom print function that records time of function call."""
    print("[{}]".format(datetime.now()), *args)

def truncated_normal(size, scale=1, limit=2):
    """Samples a tensor from an approximately truncated normal tensor.

    Arguments:
        size {tuple of ints} -- Size of desired tensor

    Keyword Arguments:
        scale {int} -- Standard deviation of normal distribution (default: {1})
        limit {int} -- Number of standard deviations to truncate at (default: {2})

    Returns:
        torch.FloatTensor -- A truncated normal sample of requested size
    """
    return torch.fmod(torch.randn(size),limit) * scale

def xavier_truncated_normal(size, limit=2, no_average=False):
    """Samples from a truncated normal where the standard deviation is automatically chosen based on size."""
    if isinstance(size, int):
        size = (size,)

    if len(size) == 1 or no_average:
        n_avg = size[-1]
    else:
        n_in, n_out = size[-2], size[-1]
        n_avg = (n_in + n_out) / 2

    return truncated_normal(size, scale=(1/n_avg)**0.5, limit=2)

def flatten(list_of_lists):
    """Turn a list of lists (or any iterable) into a flattened list."""
    return [item for sublist in list_of_lists for item in sublist]

#######################################################################
# Top k top p and minimum variance reduction
#######################################################################

def min_variance_top_k(logits, min_var_reduction = 0.0,
                       filter_value=-float('Inf'),
                       is_log_prob=False,
                       max_num_tree_beams=None):
    num_logits = logits.shape[0]
    probs, prob_inds = torch.sort(logits.exp(), descending=True)
    global_var = probs.var()
    local_vars = torch.Tensor([
        (probs[:i].var(unbiased=False) + probs[i:].var(unbiased=False))
        for i in range(1,min(num_logits,max_num_tree_beams)-1,1)
    ])

    min_idx = torch.argmin(local_vars)
    min_sep_var = local_vars[min_idx]

    # Satisfies variance criteria
    if not max_num_tree_beams: max_num_tree_beams=min_idx
    indices_to_remove = prob_inds[min(max_num_tree_beams,min_idx)+1:]
    logits[indices_to_remove] = filter_value

    return logits



def top_k_top_p_filtering(logits, top_k=0, top_p=0.0,min_var=False, filter_value=-float('Inf'), is_log_prob=False):
    """ Filter a distribution of logits using top-k and/or nucleus (top-p) filtering.
        Currently only supports a batch size of 1.
        Adapted from https://gist.github.com/thomwolf/1a5a29f6962089e871b94cbd09daf317
        Args:
            logits: logits distribution shape (vocabulary size)
            top_k >0: keep only top k tokens with highest probability (top-k filtering).
            top_p >0.0: keep the top tokens with cumulative probability >= top_p (nucleus filtering).
                Nucleus filtering is described in Holtzman et al. (http://arxiv.org/abs/1904.09751)
    """
    #logits = logits.squeeze()
    #assert logits.dim() == 1  # batch size 1 for now - could be updated for more but the code would be less clear
    top_k = min(top_k, logits.size(-1))  # Safety check
    if top_k > 0:
        # Remove all tokens with a probability less than the last token of the top-k
        indices_to_remove = logits < torch.topk(logits, top_k)[0][..., -1, None]
        logits = logits.masked_fill(indices_to_remove, filter_value)

    if top_p > 0.0:
        sorted_logits, sorted_indices = torch.sort(logits, descending=True)
        if is_log_prob:
            cumulative_probs = sorted_logits.exp().cumsum(dim=-1)
        else:
            cumulative_probs = sorted_logits.softmax(dim=-1).cumsum(dim=-1) #torch.cumsum(F.softmax(sorted_logits, dim=-1), dim=-1)

        # Remove tokens with cumulative probability above the threshold
        sorted_indices_to_remove = cumulative_probs > top_p
        # Shift the indices to the right to keep also the first token above the threshold
        sorted_indices_to_remove[..., 1:] = sorted_indices_to_remove[..., :-1].clone()
        sorted_indices_to_remove[..., 0] = 0

        indices_to_remove = sorted_indices_to_remove.scatter(-1, sorted_indices, sorted_indices_to_remove) #sorted_indices[sorted_indices_to_remove]
        logits = logits.masked_fill(indices_to_remove, filter_value)

    return logits  #.unsqueeze(0).unsqueeze(0)

#######################################################################
# Utilities for determining number of beam search beams with a budget
#######################################################################

def compute_num_beams_from_budget(vocab_size, beam, seq_len, excluded_terms):
    """TODO: Docstring for compute_num_beams.

    :vocab_size: TODO
    :init_num_beams: Number of beams if not larger than vocabulary
    :returns: TODO

    """
    num_excl = len(excluded_terms)
    vocab_size -= num_excl # Restricted vocab
    if beam <= vocab_size:
        return beam

    extra = 0
    final_beams = []
    for i in range(seq_len):
        curr_vocab = vocab_size**(i+1)
        if beam < curr_vocab and extra == 0:
            return beam[-1]

        extra_piece = extra//(seq_len - i)
        leftover = max((beam + extra_piece) - curr_vocab,0)
        new_beam = min(extra_piece + beam, curr_vocab)
        extra -= (extra_piece - leftover)

        final_beams.append(new_beam)
    return final_beams[-1]

def _set_random_seed(seed):
    """Set random seed for reproducibility."""

    assert seed != 0,"Random seed cannot be 0"
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)

def set_random_seed(args):
    """Set random seed for reproducibility."""

    seed = args.seed

    assert seed != 0,"Random seed cannot be 0"
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)


#######################################################################
# Main
#######################################################################



