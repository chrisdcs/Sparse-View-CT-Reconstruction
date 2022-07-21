# -*- coding: utf-8 -*-
"""
Created on Wed May 25 13:13:07 2022

@author: Chi Ding
"""

import torch
import torch.nn as nn
from torch.nn import init
import torch.nn.functional as F
from torch.autograd import Function
from torchvision.models import vgg19

import glob
import ctlib
import numpy as np
import os
import scipy.io as scio
from torch.utils.data import Dataset
#%% data helper function
def down_sample(sparse_view_num, full_view_folder, save_folder_name, train=True):
    """
    This function downsamples full-view data into sparse-view data
    
    Parameters
    ----------
    sparse_view_num : int, must be divisible by 1024
        DESCRIPTION.
    full_view_folder : str
        ex: 'noisy_free', 'radon_1024views'
    save_folder_name : str
        ex: 'projection'
    train : bool, optional
        generate training or test set. The default is True.

    Returns
    -------
    None.

    """
    
    # get files from full view data folder
    if train:
        file_path = r'mayo_data_low_dose_256/train'
    else:
        file_path = r'mayo_data_low_dose_256/test'
    files = sorted(glob.glob(os.path.join(file_path, full_view_folder, 'data')+'*.mat'))
    
    # get full view number and number of partitions
    l = len(files)
    sample = scio.loadmat(files[0])['data']
    full_view_num = sample.shape[0]
    n_partition = full_view_num//sparse_view_num
    
    # generate save path
    save_path = os.path.join(file_path, save_folder_name + '_' + str(sparse_view_num) + 'views')
    if not os.path.exists(save_path):
        os.mkdir(save_path)
    
    for i in range(l):
        print('downsampling', files[i][-13:])
        data = scio.loadmat(files[i])['data']
        sparse_data = np.zeros((sparse_view_num, data.shape[1]))
        for j in range(sparse_view_num):
            sparse_data[j,:] = data[j * n_partition, :]
        name = os.path.join(save_path, files[i][-13:])
        scio.savemat(name, {'data': sparse_data})
        
    print('Done!')
    
def fbp_data(sparse_view_num, prj_folder, save_folder_name, train=True):
    """
    This function applies fbp to projection data to obtain initial images: range (0,1)

    Parameters
    ----------
    sparse_view_num : int
        number of views.
    prj_folder : str
        the folder that stores projection data.
    save_folder_name : str
        the name you want to call the folder for storing fbp images.
    train : bool, optional
        apply on training/test set. The default is True.

    Returns
    -------
    None.

    """
    # get files from full view data folder
    if train:
        file_path = r'mayo_data_low_dose_256/train'
    else:
        file_path = r'mayo_data_low_dose_256/test'
    files = sorted(glob.glob(os.path.join(file_path, prj_folder, 'data')+'*.mat'))
    
    # get full view number and number of partitions
    l = len(files)
    
    # generate save path
    save_path = os.path.join(file_path, save_folder_name + '_' + str(sparse_view_num) + 'views')
    if not os.path.exists(save_path):
        os.mkdir(save_path)
    ratio = 1024//sparse_view_num
    options = torch.Tensor([sparse_view_num,512,256,256,0.006641,0.0072,0,0.006134*(ratio),2.5,2.5,0,0])
    options = options.cuda()
    mask = generate_mask(0.006641,0.0072)
    for i in range(l):
        
        data = scio.loadmat(files[i])['data']
        data = torch.FloatTensor(data.reshape(1,1,sparse_view_num,512)).cuda().contiguous()
        recon_data = ctlib.fbp(data,options)
        recon_data = recon_data.squeeze().detach().cpu().numpy()
        recon_data = recon_data * mask
        recon_data = recon_data / recon_data.max()
        recon_data = recon_data.clip(0,1)
        name = os.path.join(save_path, files[i][-13:])
        scio.savemat(name, {'data': recon_data})
        
    print('Done!')

def generate_mask(dImg, dDet):
    imgN = 256
    m = np.arange(-imgN/2+1/2,imgN/2-1/2+1,1)
    m = m**2
    mask = np.zeros((imgN,imgN))
    for i in range(imgN):
        mask[:,i] = np.sqrt(m + m[i]) * dImg
    
    detL = dDet * 512
    dedge = detL / 2 -dDet / 2
    scanR = 500 / 100 / 2
    detR = 500 / 100 / 2
    dd = dedge * scanR / np.sqrt(dedge**2 + (scanR+detR)**2)
    
    return mask <= dd
#%% data loader
class Phi_loader(Dataset):
    def __init__(self, root, file_path, sparse_view_num, train):
        self.train = train
        if train == True:
            folder = 'train'
        else:
            folder = 'test'
        
        self.file_path = file_path
        self.sparse_view_num = sparse_view_num
        self.files = sorted(glob.glob(os.path.join(root, folder, file_path, 'data')+'*.mat'))
        self.full_view_num = scio.loadmat(self.files[0])['data'].shape[0]
        self.n_partition = self.full_view_num // self.sparse_view_num
        
    
    def __getitem__(self, index):
        file = self.files[index]
        data_list = []
        data = scio.loadmat(file)['data']
        for i in range(self.n_partition):
            f_i = np.zeros((self.sparse_view_num, data.shape[1]))
            for j in range(self.sparse_view_num):
                f_i[j,:] = data[i + j*self.n_partition, :]
            data_list.append(torch.FloatTensor(f_i).unsqueeze_(0))
        
        img_file = file.replace(self.file_path, 'label_single')
        data = torch.FloatTensor(data).unsqueeze_(0)
        img = scio.loadmat(img_file)['data']
        img = torch.FloatTensor(img).unsqueeze_(0)
        
        return data, data_list, img, file[-13:]
    
    def __len__(self):
        return len(self.files)
    
class Random_loader(Dataset):
    # need projection data, ground truth and input images
    def __init__(self, root, file_path, prj_file_path, sparse_view_num, train):
        self.train = train
        if train == True:
            folder = 'train'
        else:
            folder = 'test'
        
        self.file_path = file_path
        self.prj_file_path = prj_file_path
        self.sparse_view_num = sparse_view_num
        self.files = sorted(glob.glob(os.path.join(root, folder, self.file_path, 'data')+'*.mat'))
        
        
    
    def __getitem__(self, index):
        file = self.files[index]
        file_prj = file.replace(self.file_path, self.prj_file_path)
        file_label = file.replace(self.file_path, 'label_single')
        
        input_data = scio.loadmat(file)['data']
        prj_data = scio.loadmat(file_prj)['data']/3.84
        label_data = scio.loadmat(file_label)['data']
        
        input_data = torch.FloatTensor(input_data).unsqueeze_(0)
        prj_data = torch.FloatTensor(prj_data).unsqueeze_(0)
        label_data = torch.FloatTensor(label_data).unsqueeze_(0)
        
        if self.train:
            return input_data, label_data, prj_data
        else:
            return input_data, label_data, prj_data, file[-13:]
    
    def __len__(self):
        return len(self.files)
#%% define Resnet block
class Block(nn.Module):
    def __init__(self):
        super(Block, self).__init__()
        
        # size: out channels  x in channels x filter size x filter size
        self.conv1_forward = nn.Parameter(init.xavier_normal_(torch.Tensor(32, 1, 3, 3)))
        self.conv2_forward = nn.Parameter(init.xavier_normal_(torch.Tensor(32, 32, 3, 3)))
        self.conv3_forward = nn.Parameter(init.xavier_normal_(torch.Tensor(32, 32, 3, 3)))
        self.conv4_forward = nn.Parameter(init.xavier_normal_(torch.Tensor(1, 32, 3, 3)))
        
    def forward(self, x_input):
        
        x = F.conv2d(x_input, self.conv1_forward, padding=1)
        x = F.relu(x)
        x = F.conv2d(x, self.conv2_forward, padding=1)
        x = F.relu(x)
        x = F.conv2d(x, self.conv3_forward, padding=1)
        x = F.relu(x)
        x = F.conv2d(x, self.conv4_forward, padding=1)
        # resnet structure
        x = F.relu(x + x_input)
        
        return x

class SELayer(nn.Module):
    def __init__(self, channel, reduction=16):
        super(SELayer, self).__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Sequential(
            nn.Linear(channel, channel // reduction, bias=False),
            nn.ReLU(inplace=True),
            nn.Linear(channel // reduction, channel, bias=False),
            nn.Sigmoid()
        )

    def forward(self, x):
        b, c, _, _ = x.size()
        y = self.avg_pool(x).view(b, c)
        y = self.fc(y).view(b, c, 1, 1)
        return x * y.expand_as(x)


class LongBlock(nn.Module):
    def __init__(self):
        super(LongBlock, self).__init__()
        
        # size: out channels  x in channels x filter size x filter size
        self.conv1_forward = nn.Parameter(init.xavier_normal_(torch.Tensor(32, 1, 3, 15)))
        self.conv2_forward = nn.Parameter(init.xavier_normal_(torch.Tensor(32, 32, 3, 15)))
        self.conv3_forward = nn.Parameter(init.xavier_normal_(torch.Tensor(32, 32, 3, 15)))
        self.conv4_forward = nn.Parameter(init.xavier_normal_(torch.Tensor(1, 32, 3, 15)))

        
    def forward(self, x_input):
        
        x = F.conv2d(x_input, self.conv1_forward, padding=(1,7))
        x = F.relu(x)
        x = F.conv2d(x, self.conv2_forward, padding=(1,7))
        x = F.relu(x)
        x = F.conv2d(x, self.conv3_forward, padding=(1,7))
        x = F.relu(x)
        x = F.conv2d(x, self.conv4_forward, padding=(1,7))
        # resnet structure
        x = x + x_input
        
        return x

# define the simple resnet
class ResNet(torch.nn.Module):
    def __init__(self, LayerNo):
        super(ResNet, self).__init__()
        onelayer = []
        self.LayerNo = LayerNo
        
        for i in range(LayerNo):
            onelayer.append(LongBlock())
        
        self.fcs = nn.ModuleList(onelayer)
        
    def forward(self, x):
        for i in range(self.LayerNo):
            # resnet architecture
            x = self.fcs[i](x)
        return x
#%% U-Net
class DoubleConv(nn.Module):
    def __init__(self, in_c, out_c):
        super(DoubleConv, self).__init__()
        self.conv = nn.Sequential(
                nn.Conv2d(in_channels=in_c, out_channels=out_c, kernel_size=3, padding=1),
                nn.BatchNorm2d(out_c), 
                nn.ReLU(inplace=True),
                nn.Conv2d(in_channels=out_c, out_channels=out_c, kernel_size=3, padding=1),
                nn.BatchNorm2d(out_c),
                nn.ReLU(inplace=True)
            )
        
    def forward(self, x):
        return self.conv(x)
    
    
class Down(nn.Module):
    def __init__(self, in_c):
        super(Down, self).__init__()
        self.pool = nn.MaxPool2d(kernel_size=2, stride=2)
        self.conv = DoubleConv(in_c, in_c * 2)
        
        
    def forward(self, x):
        out = self.conv(self.pool(x))
        return out
        
class Up(nn.Module):
    def __init__(self, in_c):
        super(Up, self).__init__()
        self.up_conv =  nn.ConvTranspose2d(in_channels=in_c, out_channels=in_c//2, 
                                           kernel_size=2, stride=2)
        self.conv = DoubleConv(in_c, in_c//2)
        
    def forward(self, x1, x2):
        x1 = self.up_conv(x1)
        # input is CHW
        diffY = x2.size()[2] - x1.size()[2]
        diffX = x2.size()[3] - x1.size()[3]

        x1 = F.pad(x1, [diffX // 2, diffX - diffX // 2,
                        diffY // 2, diffY - diffY // 2])
        x = torch.cat([x2, x1], dim=1)
        return self.conv(x)
    
class OutConv(nn.Module):
    def __init__(self, in_channels, out_channels):
        super(OutConv, self).__init__()
        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size=1)

    def forward(self, x):
        return self.conv(x)

class UNet(nn.Module):
    def __init__(self, n_channels):
        super(UNet, self).__init__()
        self.n_channels = n_channels

        self.inc = DoubleConv(n_channels, 64)
        self.down1 = Down(64)
        self.down2 = Down(128)
        
        self.up1 = Up(256)
        self.up2 = Up(128)
        self.outc = OutConv(64, 1)

    def forward(self, x):
        x1 = self.inc(x)
        x2 = self.down1(x1)
        x3 = self.down2(x2)
        x = self.up1(x3, x2)
        x = self.up2(x, x1)
        out = self.outc(x)
        return out
    
#%% LDA
# class gradient_weighted(Function):
#     @staticmethod
#     def forward(self, input_data, alpha, weights, proj, options):
#         diff = ctlib.projection(input_data, options) - proj
#         temp = weights * diff
#         intervening_res = ctlib.projection_t(temp, options)
#         self.save_for_backward(intervening_res, alpha, diff, weights, options)
#         out = input_data - alpha * intervening_res
#         return out

#     @staticmethod
#     def backward(self, grad_output):
#         intervening_res, alpha, diff, weights, options = self.saved_tensors
#         temp = ctlib.projection(grad_output, options)
#         temp = weights * temp
#         temp = ctlib.projection_t(temp,options)
#         grad_input = grad_output - alpha * temp
        
#         temp = intervening_res * grad_output
#         grad_alpha = - temp.sum().view(-1)
        
        
        
#         return grad_input, grad_alpha, ,None, None

class prj_fun(Function):
    @staticmethod
    def forward(self, input_data, weight, proj, options):
        temp = ctlib.projection(input_data, options) - proj
        # print(temp)
        intervening_res = ctlib.projection_t(temp, options)
        self.save_for_backward(intervening_res, weight, options)
        out = input_data - weight * intervening_res
        return out

    @staticmethod
    def backward(self, grad_output):
        intervening_res, weight, options = self.saved_tensors
        temp = ctlib.projection(grad_output, options)
        temp = ctlib.projection_t(temp, options)
        grad_input = grad_output - weight * temp
        temp = intervening_res * grad_output
        grad_weight = - temp.sum().view(-1)
        return grad_input, grad_weight, None, None
    
class projection(Function):
    @staticmethod
    def forward(self, input_data, options):
        # y = Ax   x = A^T y
        out = ctlib.projection(input_data, options)
        self.save_for_backward(options, input_data)
        return out

    @staticmethod
    def backward(self, grad_output):
        options, input_data = self.saved_tensors
        grad_input = ctlib.projection_t(grad_output, options)
        return grad_input, None
    
    

class LDA_weighted(torch.nn.Module):
    def __init__(self, LayerNo, PhaseNo, sparse_view_num, alpha, beta):
        super(LDA_weighted, self).__init__()
        
        # soft threshold
        self.soft_thr = nn.Parameter(torch.Tensor([0.002]))
        # sparcity bactracking
        self.gamma = 1.0
        # a parameter for backtracking
        self.sigma = 10**6
        # parameter for activation function
        self.delta = 0.001
        # set phase number
        self.PhaseNo = PhaseNo
        self.init = True
        
        self.alphas = nn.Parameter(alpha * torch.ones(LayerNo))
        self.betas = nn.Parameter(beta * torch.ones(LayerNo),requires_grad=False)
        
        weights = torch.tensor([100, 1, 1/3, 1/5, 1/5, 1/5, 1/3, 1]) / 100.
        weight_matrix = torch.ones((512,512))
        for i in range(8):
            for j in range(64):
                weight_matrix[i+j*8,:] *= weights[i]
        
        self.weight_matrix = nn.Parameter(weight_matrix[None,:,:],requires_grad=False)
        
        # size: out channels  x in channels x filter size x filter size
        # every block shares weights
        self.conv1 = nn.Parameter(init.xavier_normal_(torch.Tensor(32, 1, 3, 3)))
        self.conv2 = nn.Parameter(init.xavier_normal_(torch.Tensor(32, 32, 3, 3)))
        self.conv3 = nn.Parameter(init.xavier_normal_(torch.Tensor(32, 32, 3, 3)))
        self.conv4 = nn.Parameter(init.xavier_normal_(torch.Tensor(32, 32, 3, 3)))
        
        self.projection = projection()
        self.grad_step = prj_fun()
        self.sparse_view_num = sparse_view_num
        ratio = 1024 // (sparse_view_num * 8)
        
        options = torch.tensor([sparse_view_num * 8, 512, 256, 256, 0.006641,
                                                 0.0072, 0, 0.006134 * ratio, 2.5, 2.5, 0, 0])
        self.options_sparse_view = nn.Parameter(options, requires_grad=False)
    
    def set_PhaseNo(self, PhaseNo):
        # used when adding more phases
        self.PhaseNo = PhaseNo
        
    def set_init(self, init):
        self.init = init
        
    def activation(self, x):
        """ activation function from eq. (33) in paper """
        
        # index for x < -delta and x > delta
        index = torch.sign(F.relu(torch.abs(x)-self.delta))
        output = index * F.relu(x)
        # add parts when -delta <= x <= delta
        output += (1-index) * (1/(4*self.delta) * torch.square(x) + 1/2 * x + self.delta/4)
        return output
    
    def activation_der(self, x):
        """ derivative of activation function from eq. (33) in paper """
        
        # index for x < -delta and x > delta
        index = torch.sign(F.relu(torch.abs(x)-self.delta))
        output = index * torch.sign(F.relu(x))
        # add parts when -delta <= x <= delta
        output += (1-index) * (1/(2 * self.delta) * x + 1/2)
        return output
    
    def grad_r(self, x):
        """ implementation of eq. (10) in paper  """
        
        # first obtain forward passs to get features g_i, i = 1, 2, ..., n_c
        # This is the feature extraction map, we can change it to other networks
        # x_input: n x 1 x 33 x 33
        x_input = x#.view(-1, 1, 33, 33)
        soft_thr = self.soft_thr * self.gamma
        
        # shape from input to output: batch size x height x width x n channels
        x1 = F.conv2d(x_input, self.conv1, padding = 1)                 # (batch,  1, h, w) -> (batch, 32, h, w)
        x2 = F.conv2d(self.activation(x1), self.conv2, padding = 1)     # (batch, 32, h, w) -> (batch, 32, h, w)
        x3 = F.conv2d(self.activation(x2), self.conv3, padding = 1)     # (batch, 32, h, w) -> (batch, 32, h, w)
        g = F.conv2d(self.activation(x3), self.conv4, padding = 1)      # (batch, 32, h, w) -> (batch, 32, h, w)
        n_channel = g.shape[1]
        
        # compute norm over channel and compute g_factor
        norm_g = torch.norm(g, dim = 1)
        I1 = torch.sign(F.relu(norm_g - soft_thr))[:,None,:,:]
        I1 = torch.tile(I1, [1, n_channel, 1, 1])
        I0 = 1 - I1
        
        g_factor = I1 * F.normalize(g, dim=1) + I0 * g / soft_thr
        
        # implementation for eq. (9): multiply grad_g to g_factor from the left
        # result derived from chain rule and that gradient of convolution is convolution transpose
        g_r = F.conv_transpose2d(g_factor, self.conv4, padding = 1)
        g_r *= self.activation_der(x3)
        g_r = F.conv_transpose2d(g_r, self.conv3, padding = 1)
        g_r *= self.activation_der(x2)
        g_r = F.conv_transpose2d(g_r, self.conv2, padding = 1)
        g_r *= self.activation_der(x1)
        g_r = F.conv_transpose2d(g_r, self.conv1, padding = 1) 
        
        return g_r#.reshape(-1, 1089)
    
    def R(self, x):
        """ implementation of eq. (9) in paper: the smoothed regularizer  """
        
        # first obtain forward passs to get features g_i, i = 1, 2, ..., n_c
        # x_input: n x 1 x 33 x 33
        x_input = x#.view(-1, 1, 33, 33)
        soft_thr = self.soft_thr * self.gamma
        
        # shape from input to output: batch size x height x width x n channels
        x1 = F.conv2d(x_input, self.conv1, padding = 1)                 # (batch,  1, h, w) -> (batch, 32, h, w)
        x2 = F.conv2d(self.activation(x1), self.conv2, padding = 1)     # (batch, 32, h, w) -> (batch, 32, h, w)
        x3 = F.conv2d(self.activation(x2), self.conv3, padding = 1)     # (batch, 32, h, w) -> (batch, 32, h, w)
        g = F.conv2d(self.activation(x3), self.conv4, padding = 1)      # (batch, 32, h, w) -> (batch, 32, h, w)
        
        norm_g = torch.norm(g, dim = 1)
        I1 = torch.sign(F.relu(norm_g - soft_thr))
        I0 = 1 - I1
        
        r = 1/(2 * soft_thr) * torch.square(norm_g) * I0 + (norm_g - soft_thr) * I1
        r = r.reshape(-1, 65536)
        r = torch.sum(r, -1, keepdim=True)
        
        return r
    
    def phi(self, x, proj):
        """ The implementation for the loss function """
        # x is the reconstruction result
        # proj is the ground truth
        
        r = self.R(x)
        f = 1/2 * torch.sum((torch.square(
            self.projection.apply(x, self.options_sparse_view) - proj)).reshape(-1,262144), 
                            dim = 1, keepdim=True)
        
        
        return f + r
    
    def phase(self, x, proj, phase):
        """
        x is the reconstruction output from last phase
        proj is Phi True_x, the sampled ground truth
        
        """
        alpha = torch.abs(self.alphas[phase])
        beta = torch.abs(self.betas[phase])
        
        # Implementation of eq. 2/7 (ISTANet paper) Immediate reconstruction
        # here we obtain z (in LDA paper from eq. 12)
        z = ctlib.projection(x, self.options_sparse_view) - proj
        z = z * self.weight_matrix.repeat(z.shape[0],1,1,1)
        z = x - alpha * ctlib.projection_t(z, self.options_sparse_view)
        
        # gradient of r, the smoothed regularizer
        grad_r_z = self.grad_r(z)
        # u: resnet structure
        u = z - beta * grad_r_z
        
        if not self.init:
            grad_r_x = self.grad_r(x)
            v = z - alpha * grad_r_x
            
            """ The rest is to just find out phi(u) and phi(v), which one is smaller """
            phi_u = self.phi(u, proj)
            phi_v = self.phi(v, proj)
            
            u_ind = torch.sign(F.relu(phi_v - phi_u))
            v_ind = 1 - u_ind
            u_ind = u_ind.reshape(-1,1,1,1)
            v_ind = v_ind.reshape(-1,1,1,1)
            x_next = u_ind * u + v_ind * v
        else:
            x_next = u
        
        """ update soft threshold, step 7-8 algorithm 1 """
        norm_grad_phi_x_next = \
                        torch.norm(
                                    (ctlib.projection_t(
                                        ctlib.projection(
                                            x_next, self.options_sparse_view)-proj, 
                                        self.options_sparse_view)
                                     + self.grad_r(x_next)).reshape(-1,65536),
                                    dim = -1, keepdim= True
                                    )
        sig_gam_eps = self.sigma * self.gamma * self.soft_thr 
        self.gamma *= 0.9 if (torch.mean(norm_grad_phi_x_next) < sig_gam_eps) else 1.0
        
        return x_next, proj
    
    def forward(self, x, proj, dummy=None):
        
        # x is initial given by [Phi f0*, Phi^2 f0*, .., Phi^r-1 f0*]
        # proj is the projection data input, i.e. f0*
        x_list = []
        proj_list = []
        for phase in range(self.PhaseNo):
            x, proj = self.phase(x, proj, phase)
            x_list.append(x)
            proj_list.append(proj)
            
        return x_list, proj_list

class LDA(torch.nn.Module):
    def __init__(self, LayerNo, PhaseNo, sparse_view_num, alpha, beta):
        super(LDA, self).__init__()
        
        # soft threshold
        self.soft_thr = nn.Parameter(torch.Tensor([0.002]))
        # sparcity bactracking
        self.gamma = 1.0
        # a parameter for backtracking
        self.sigma = 10**6
        # parameter for activation function
        self.delta = 0.001
        # set phase number
        self.PhaseNo = PhaseNo
        self.init = True
        
        self.alphas = nn.Parameter(alpha * torch.ones(LayerNo))
        self.betas = nn.Parameter(beta * torch.ones(LayerNo))
        
        # size: out channels  x in channels x filter size x filter size
        # every block shares weights
        self.conv1 = nn.Parameter(init.xavier_normal_(torch.Tensor(32, 1, 3, 3)))
        self.conv2 = nn.Parameter(init.xavier_normal_(torch.Tensor(32, 32, 3, 3)))
        self.conv3 = nn.Parameter(init.xavier_normal_(torch.Tensor(32, 32, 3, 3)))
        self.conv4 = nn.Parameter(init.xavier_normal_(torch.Tensor(32, 32, 3, 3)))
        
        self.projection = projection()
        self.grad_step = prj_fun()
        self.sparse_view_num = sparse_view_num
        
        ratio = 1024 // (sparse_view_num * 8)
        
        options = torch.tensor([sparse_view_num * 8, 512, 256, 256, 0.006641,
                                                 0.0072, 0, 0.006134 * ratio, 2.5, 2.5, 0, 0])
        self.options_sparse_view = nn.Parameter(options, requires_grad=False)
    
    def set_PhaseNo(self, PhaseNo):
        # used when adding more phases
        self.PhaseNo = PhaseNo
        
    def set_init(self, init):
        self.init = init
        
    def activation(self, x):
        """ activation function from eq. (33) in paper """
        
        # index for x < -delta and x > delta
        index = torch.sign(F.relu(torch.abs(x)-self.delta))
        output = index * F.relu(x)
        # add parts when -delta <= x <= delta
        output += (1-index) * (1/(4*self.delta) * torch.square(x) + 1/2 * x + self.delta/4)
        return output
    
    def activation_der(self, x):
        """ derivative of activation function from eq. (33) in paper """
        
        # index for x < -delta and x > delta
        index = torch.sign(F.relu(torch.abs(x)-self.delta))
        output = index * torch.sign(F.relu(x))
        # add parts when -delta <= x <= delta
        output += (1-index) * (1/(2 * self.delta) * x + 1/2)
        return output
    
    def grad_r(self, x):
        """ implementation of eq. (10) in paper  """
        
        # first obtain forward passs to get features g_i, i = 1, 2, ..., n_c
        # This is the feature extraction map, we can change it to other networks
        # x_input: n x 1 x 33 x 33
        x_input = x#.view(-1, 1, 33, 33)
        soft_thr = self.soft_thr * self.gamma
        
        # shape from input to output: batch size x height x width x n channels
        x1 = F.conv2d(x_input, self.conv1, padding = 1)                 # (batch,  1, h, w) -> (batch, 32, h, w)
        x2 = F.conv2d(self.activation(x1), self.conv2, padding = 1)     # (batch, 32, h, w) -> (batch, 32, h, w)
        x3 = F.conv2d(self.activation(x2), self.conv3, padding = 1)     # (batch, 32, h, w) -> (batch, 32, h, w)
        g = F.conv2d(self.activation(x3), self.conv4, padding = 1)      # (batch, 32, h, w) -> (batch, 32, h, w)
        n_channel = g.shape[1]
        
        # compute norm over channel and compute g_factor
        norm_g = torch.norm(g, dim = 1)
        I1 = torch.sign(F.relu(norm_g - soft_thr))[:,None,:,:]
        I1 = torch.tile(I1, [1, n_channel, 1, 1])
        I0 = 1 - I1
        
        g_factor = I1 * F.normalize(g, dim=1) + I0 * g / soft_thr
        
        # implementation for eq. (9): multiply grad_g to g_factor from the left
        # result derived from chain rule and that gradient of convolution is convolution transpose
        g_r = F.conv_transpose2d(g_factor, self.conv4, padding = 1)
        g_r *= self.activation_der(x3)
        g_r = F.conv_transpose2d(g_r, self.conv3, padding = 1)
        g_r *= self.activation_der(x2)
        g_r = F.conv_transpose2d(g_r, self.conv2, padding = 1)
        g_r *= self.activation_der(x1)
        g_r = F.conv_transpose2d(g_r, self.conv1, padding = 1) 
        
        return g_r#.reshape(-1, 1089)
    
    def R(self, x):
        """ implementation of eq. (9) in paper: the smoothed regularizer  """
        
        # first obtain forward passs to get features g_i, i = 1, 2, ..., n_c
        # x_input: n x 1 x 33 x 33
        x_input = x#.view(-1, 1, 33, 33)
        soft_thr = self.soft_thr * self.gamma
        
        # shape from input to output: batch size x height x width x n channels
        x1 = F.conv2d(x_input, self.conv1, padding = 1)                 # (batch,  1, h, w) -> (batch, 32, h, w)
        x2 = F.conv2d(self.activation(x1), self.conv2, padding = 1)     # (batch, 32, h, w) -> (batch, 32, h, w)
        x3 = F.conv2d(self.activation(x2), self.conv3, padding = 1)     # (batch, 32, h, w) -> (batch, 32, h, w)
        g = F.conv2d(self.activation(x3), self.conv4, padding = 1)      # (batch, 32, h, w) -> (batch, 32, h, w)
        
        norm_g = torch.norm(g, dim = 1)
        I1 = torch.sign(F.relu(norm_g - soft_thr))
        I0 = 1 - I1
        
        r = 1/(2 * soft_thr) * torch.square(norm_g) * I0 + (norm_g - soft_thr) * I1
        r = r.reshape(-1, 65536)
        r = torch.sum(r, -1, keepdim=True)
        
        return r
    
    def phi(self, x, proj):
        """ The implementation for the loss function """
        """ The implementation for the loss function """
        # x is the reconstruction result
        # proj is the ground truth
        
        r = self.R(x)
        f = 1/2 * torch.sum((torch.square(
            self.projection.apply(x, self.options_sparse_view) - proj)).reshape(-1,262144), 
                            dim = 1, keepdim=True)
        
        return f + r
    
    def phase(self, x, proj, phase):
        """
        x is the reconstruction output from last phase
        proj is Phi True_x, the sampled ground truth
        
        """
        alpha = torch.abs(self.alphas[phase])
        beta = torch.abs(self.betas[phase])
        
        # Implementation of eq. 2/7 (ISTANet paper) Immediate reconstruction
        # here we obtain z (in LDA paper from eq. 12)
        z = self.grad_step.apply(x, alpha, proj, self.options_sparse_view)
        # z = z / z.max()
        # z = z.clip(0,1)
        
        
        # print('z',z)
        # z = x - alpha * torch.mm(x, PhiTPhi)
        # z = z + alpha * PhiTb
        
        # gradient of r, the smoothed regularizer
        grad_r_z = self.grad_r(z)
        tau = alpha * beta / (alpha + beta)
        # u: resnet structure
        u = z - tau * grad_r_z
        
        if not self.init:
            grad_r_x = self.grad_r(x)
            v = z - alpha * grad_r_x
            
            """ The rest is to just find out phi(u) and phi(v), which one is smaller """
            phi_u = self.phi(u, proj)
            phi_v = self.phi(v, proj)
            
            u_ind = torch.sign(F.relu(phi_v - phi_u))
            v_ind = 1 - u_ind
            
            x_next = u_ind * u + v_ind * v
        else:
            x_next = u
        
        """ update soft threshold, step 7-8 algorithm 1 """
        norm_grad_phi_x_next = \
                        torch.norm(
                                    (ctlib.projection_t(
                                        ctlib.projection(
                                            x_next, self.options_sparse_view)-proj, 
                                        self.options_sparse_view)
                                     + self.grad_r(x_next)).reshape(-1,65536),
                                    dim = -1, keepdim= True
                                    )
        sig_gam_eps = self.sigma * self.gamma * self.soft_thr 
        self.gamma *= 0.9 if (torch.mean(norm_grad_phi_x_next) < sig_gam_eps) else 1.0
        
        return x_next, proj
    
    def forward(self, x, proj, mask):
        
        # x is initial given by [Phi f0*, Phi^2 f0*, .., Phi^r-1 f0*]
        # proj is the projection data input, i.e. f0*
        x_list = []
        proj_list = []
        for phase in range(self.PhaseNo):
            x, proj = self.phase(x, proj, phase)
            x_list.append(x)
            proj_list.append(proj)
            
        return x_list, proj_list
