from __future__ import division
import numpy as np
from integrals import *
from scipy.misc import factorial2 as fact2
from scipy.linalg import fractional_matrix_power as mat_pow
from scipy.linalg import eig, eigh 
from scipy.misc import factorial
from tqdm import tqdm, trange 
import itertools
import matplotlib
matplotlib.use('TkAgg')

class BasisFunction(object):
    def __init__(self,origin=(0,0,0),shell=(0,0,0),exps=[],coefs=[]):
        assert len(origin)==3
        assert len(shell)==3
        self.origin = np.asarray(origin,'d')#*1.889725989 # to bohr
        self.shell = np.asarray(shell).astype(int)
        self.exps  = exps
        self.coefs = coefs
        self.normalize()

    def normalize(self):
        l,m,n = self.shell
        # self.norm is a list of length number primitives
        self.norm = np.sqrt(np.power(2,2*(l+m+n)+1.5)*
                        np.power(self.exps,l+m+n+1.5)/
                        fact2(2*l-1)/fact2(2*m-1)/
                        fact2(2*n-1)/np.power(np.pi,1.5))
        return

class Molecule(object):
    def __init__(self,filename,basis='sto3g',gauge=None,giao=False):
        charge, multiplicity, atomlist = self.read_molecule(filename)
        self.charge = charge
        self.multiplicity = multiplicity
        self.atoms = atomlist
        self.nelec = sum([atom[0] for atom in atomlist]) - charge 
        self.nocc  = self.nelec//2
        self.bfs = []
        self.is_built = False
        self.giao = giao
        try:
            import data
        except ImportError:
            print "No basis set data"
            sys.exit(0)

        basis_data = data.basis[basis]
        for atom in self.atoms:
            for momentum,prims in basis_data[atom[0]]:
                exps = [e for e,c in prims]
                coefs = [c for e,c in prims]
                for shell in self.momentum2shell(momentum):
                    #self.bfs.append(BasisFunction(atom[1],shell,exps,coefs))
                    self.bfs.append(BasisFunction(np.asarray(atom[1]),np.asarray(shell),np.asarray(exps),np.asarray(coefs)))
        self.nbasis = len(self.bfs)
        # note this is center of positive charge
        self.center_of_charge = np.asarray([sum([x[0]*x[1][0] for x in self.atoms]),
                                            sum([x[0]*x[1][1] for x in self.atoms]),
                                            sum([x[0]*x[1][2] for x in self.atoms])])\
                                         * (1./sum([x[0] for x in self.atoms]))
        if not gauge:
            self.gauge_origin = self.center_of_charge
        else:
            self.gauge_origin = np.asarray(gauge)
           

    def build(self):
        # routine to build necessary integrals
        self.one_electron_integrals()
        self.two_electron_integrals()
        if self.giao:
            self.GIAO_one_electron_integrals()
            self.GIAO_two_electron_integrals()
        self.is_built = True

    def momentum2shell(self,momentum):
        shells = {
            'S' : [(0,0,0)],
            'P' : [(1,0,0),(0,1,0),(0,0,1)],
            'D' : [(2,0,0),(1,1,0),(1,0,1),(0,2,0),(0,1,1),(0,0,2)],
            'F' : [(3,0,0),(2,1,0),(2,0,1),(1,2,0),(1,1,1),(1,0,2),
                   (0,3,0),(0,2,1),(0,1,2), (0,0,3)]
            }
        return shells[str(momentum)]
        
    def sym2num(self,sym):
        symbol = [
            "X","H","He",
            "Li","Be","B","C","N","O","F","Ne",
            "Na","Mg","Al","Si","P","S","Cl","Ar",
            "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe",
            "Co", "Ni", "Cu", "Zn",
            "Ga", "Ge", "As", "Se", "Br", "Kr",
            "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru",
            "Rh", "Pd", "Ag", "Cd",
            "In", "Sn", "Sb", "Te", "I", "Xe",
            "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm",  "Eu",
            "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu",
            "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
            "Tl","Pb","Bi","Po","At","Rn"]
        return symbol.index(str(sym))
        
    def read_molecule(self,filename):
        with open(filename) as f:
            atomlist = []
            for line_number,line in enumerate(f):
                if line_number == 0:
                    assert len(line.split()) == 1
                    natoms = int(line.split()[0])
                elif line_number == 1:
                    assert len(line.split()) == 2
                    charge = int(line.split()[0])
                    multiplicity = int(line.split()[1])
                else: 
                    if len(line.split()) == 0: break
                    assert len(line.split()) == 4
                    sym = self.sym2num(str(line.split()[0]))
                    x   = float(line.split()[1])*1.889725989
                    y   = float(line.split()[2])*1.889725989
                    z   = float(line.split()[3])*1.889725989
                    #atomlist.append((sym,(x,y,z)))
                    atomlist.append((sym,np.asarray([x,y,z])))
    
        return charge, multiplicity, atomlist
    def one_electron_integrals(self):
        N = self.nbasis
        # core integrals
        self.S = np.zeros((N,N)) 
        self.rH = np.zeros((3,N,N)) 
        self.V = np.zeros((N,N)) 
        self.T = np.zeros((N,N)) 
        # dipole integrals
        self.Mx = np.zeros((N,N)) 
        self.My = np.zeros((N,N)) 
        self.Mz = np.zeros((N,N)) 
 
        # GIAO quasienergy dipole intgrals
        self.rDipX = np.zeros((3,N,N))
        self.rDipY = np.zeros((3,N,N))
        self.rDipZ = np.zeros((3,N,N))
        
        # angular momentum
        self.L = np.zeros((3,N,N)) 

        self.nuc_energy = 0.0
        # Get one electron integrals
        print "One-electron integrals"

        for i in tqdm(range(N)):
            for j in range(i+1):
                self.S[i,j] = self.S[j,i] \
                    = S(self.bfs[i],self.bfs[j])
                self.T[i,j] = self.T[j,i] \
                    = T(self.bfs[i],self.bfs[j])
                self.Mx[i,j] = self.Mx[j,i] \
                    = Mu(self.bfs[i],self.bfs[j],'x',gOrigin=self.gauge_origin)
                self.My[i,j] = self.My[j,i] \
                    = Mu(self.bfs[i],self.bfs[j],'y',gOrigin=self.gauge_origin)
                self.Mz[i,j] = self.Mz[j,i] \
                    = Mu(self.bfs[i],self.bfs[j],'z',gOrigin=self.gauge_origin)
                for atom in self.atoms:
                    self.V[i,j] += -atom[0]*V(self.bfs[i],self.bfs[j],atom[1])
                self.V[j,i] = self.V[i,j]

                # RxDel is antisymmetric
                self.L[0,i,j] \
                    = RxDel(self.bfs[i],self.bfs[j],self.gauge_origin,'x')
                self.L[1,i,j] \
                    = RxDel(self.bfs[i],self.bfs[j],self.gauge_origin,'y')
                self.L[2,i,j] \
                    = RxDel(self.bfs[i],self.bfs[j],self.gauge_origin,'z')

                self.L[:,j,i] = -1*self.L[:,i,j] 


        # Also populate nuclear repulsion at this time
        for pair in itertools.combinations(self.atoms,2):
            self.nuc_energy += pair[0][0]*pair[1][0]/np.linalg.norm(pair[0][1] - pair[1][1])
           
        # preparing for SCF
        self.Core       = self.T + self.V
        self.X          = mat_pow(self.S,-0.5)
        self.U          = mat_pow(self.S,0.5)

    def GIAO_one_electron_integrals(self):
        N = self.nbasis

        #GIAO overlap
        self.Sb = np.zeros((3,N,N))

        # derivative of one-electron GIAO integrals wrt B at B = 0.
        self.rH = np.zeros((3,N,N)) 

        # London Angular momentum L_N
        self.Ln = np.zeros((3,N,N))

        # holds total dH/dB = 0.5*Ln + rH
        self.dhdb = np.zeros((3,N,N))

        print "GIAO one-electron integrals"
        for i in tqdm(range(N)):
            for j in range(N):
                #QAB matrix elements
                XAB = self.bfs[i].origin[0] - self.bfs[j].origin[0]
                YAB = self.bfs[i].origin[1] - self.bfs[j].origin[1]
                ZAB = self.bfs[i].origin[2] - self.bfs[j].origin[2]
                # GIAO T
                self.rH[0,i,j] = T(self.bfs[i],self.bfs[j],n=(1,0,0),gOrigin=self.gauge_origin)
                self.rH[1,i,j] = T(self.bfs[i],self.bfs[j],n=(0,1,0),gOrigin=self.gauge_origin)
                self.rH[2,i,j] = T(self.bfs[i],self.bfs[j],n=(0,0,1),gOrigin=self.gauge_origin)

                for atom in self.atoms:
                    # GIAO V
                    self.rH[0,i,j] += -atom[0]*V(self.bfs[i],self.bfs[j],atom[1],n=(1,0,0),gOrigin=self.gauge_origin)
                    self.rH[1,i,j] += -atom[0]*V(self.bfs[i],self.bfs[j],atom[1],n=(0,1,0),gOrigin=self.gauge_origin)
                    self.rH[2,i,j] += -atom[0]*V(self.bfs[i],self.bfs[j],atom[1],n=(0,0,1),gOrigin=self.gauge_origin)

                # Some temp copies for mult with QAB matrix 
                xH = self.rH[0,i,j]
                yH = self.rH[1,i,j]
                zH = self.rH[2,i,j]
               
                # add QAB contribution 
                self.rH[0,i,j] = 0.5*(-ZAB*yH + YAB*zH)
                self.rH[1,i,j] = 0.5*( ZAB*xH - XAB*zH)
                self.rH[2,i,j] = 0.5*(-YAB*xH + XAB*yH)

                # add QAB contribution for overlaps 
                #C = np.asarray([0,0,0])
                Rx = S(self.bfs[i],self.bfs[j],n=(1,0,0),gOrigin=self.gauge_origin)
                Ry = S(self.bfs[i],self.bfs[j],n=(0,1,0),gOrigin=self.gauge_origin)
                Rz = S(self.bfs[i],self.bfs[j],n=(0,0,1),gOrigin=self.gauge_origin)
                self.Sb[0,i,j] = 0.5*(-ZAB*Ry + YAB*Rz)
                self.Sb[1,i,j] = 0.5*( ZAB*Rx - XAB*Rz)
                self.Sb[2,i,j] = 0.5*(-YAB*Rx + XAB*Ry)

                # now do Angular London Momentum
                self.Ln[0,i,j] = RxDel(self.bfs[i],self.bfs[j],self.gauge_origin,'x',london=True)
                self.Ln[1,i,j] = RxDel(self.bfs[i],self.bfs[j],self.gauge_origin,'y',london=True)
                self.Ln[2,i,j] = RxDel(self.bfs[i],self.bfs[j],self.gauge_origin,'z',london=True)

        # below gives dH/dB accoriding to dalton
        self.dhdb[:] = 0.5*self.Ln[:] + self.rH[:]

    def two_electron_integrals(self):
        N = self.nbasis
        self.TwoE = np.zeros((N,N,N,N))  
        print "Two-electron integrals"
        self.TwoE = doERIs(N,self.TwoE,self.bfs)
        self.TwoE = np.asarray(self.TwoE)

    def GIAO_two_electron_integrals(self):
        N = self.nbasis
        self.GR1 = np.zeros((3,N,N,N,N))  
        self.GR2 = np.zeros((3,N,N,N,N))  
        self.dgdb = np.zeros((3,N,N,N,N))  
        print "GIAO two-electron integrals"
        self.dgdb = do2eGIAO(N,self.GR1,self.GR2,self.dgdb,self.bfs,self.gauge_origin)
        self.dgdb = np.asarray(self.dgdb)

