from __future__ import division
from __future__ import print_function
import numpy as np
import scipy
import sys
from itertools import product, combinations
from bitstring import BitArray
from mmd.slater import common_index, get_excitation
from mmd.utils.davidson import davidson
from scipy.special import comb
from scipy.linalg import sqrtm, lu

class PostSCF(object):
    """Class for post-scf routines"""
    def __init__(self,mol):
        self.mol = mol
        if not self.mol.is_converged:
            sys.exit("SCF not converged, skipping Post-SCF")
        self.ao2mo()

    def ao2mo(self):
        """Routine to convert AO integrals to MO integrals"""
        self.mol.single_bar = np.einsum('mp,mnlz->pnlz',
                                        self.mol.C,self.mol.TwoE)
        temp = np.einsum('nq,pnlz->pqlz',
                         self.mol.C,self.mol.single_bar)
        self.mol.single_bar = np.einsum('lr,pqlz->pqrz',
                                        self.mol.C,temp)
        temp = np.einsum('zs,pqrz->pqrs',
                         self.mol.C,self.mol.single_bar)
        self.mol.single_bar = temp

        # TODO: Make this tx more elegant?
        # tile spin to make spin orbitals from spatial (twice dimension)

        self.mol.norb = self.mol.nbasis * 2 # spin orbital

        self.mol.double_bar = np.zeros([2*idx for idx in self.mol.single_bar.shape])
        for p in range(self.mol.double_bar.shape[0]):
            for q in range(self.mol.double_bar.shape[1]):
                for r in range(self.mol.double_bar.shape[2]):
                    for s in range(self.mol.double_bar.shape[3]):
                        value1 = self.mol.single_bar[p//2,r//2,q//2,s//2].real * (p%2==r%2) * (q%2==s%2)
                        value2 = self.mol.single_bar[p//2,s//2,q//2,r//2].real * (p%2==s%2) * (q%2==r%2)
                        self.mol.double_bar[p,q,r,s] = value1 - value2

        # create Hp, the spin basis one electron operator 
        spin = np.eye(2)
        self.mol.Hp = np.kron(np.einsum('uj,vi,uv', self.mol.C, self.mol.C, self.mol.Core).real,spin)

        # create fs, the spin basis fock matrix eigenvalues 
        self.mol.fs = np.kron(np.diag(self.mol.MO),spin)

    
    def MP2(self,spin_orbital=False):
        """Routine to compute MP2 energy from RHF reference"""
        if spin_orbital:
            # Use spin orbitals from RHF reference
            EMP2 = 0.0
            occupied = range(self.mol.nelec)
            virtual  = range(self.mol.nelec,self.mol.norb)
            for i,j,a,b in product(occupied,occupied,virtual,virtual):
                denom = self.mol.fs[i,i] + self.mol.fs[j,j] \
                      - self.mol.fs[a,a] - self.mol.fs[b,b]
                numer = self.mol.double_bar[i,j,a,b]**2 
                EMP2 += numer/denom

            self.mol.emp2 = 0.25*EMP2 + self.mol.energy   
        else:
            # Use spatial orbitals from RHF reference
            EMP2 = 0.0
            occupied = range(self.mol.nocc)
            virtual  = range(self.mol.nocc,self.mol.nbasis)
            for i,j,a,b in product(occupied,occupied,virtual,virtual):
                denom = self.mol.MO[i] + self.mol.MO[j] \
                      - self.mol.MO[a] - self.mol.MO[b]
                numer = self.mol.single_bar[i,a,j,b] \
                      * (2.0*self.mol.single_bar[i,a,j,b] 
                        - self.mol.single_bar[i,b,j,a])
                EMP2 += numer/denom
            self.mol.emp2 = EMP2 + self.mol.energy   

        print('E(MP2) = ', self.mol.emp2.real) 

    @staticmethod
    def tuple2bitstring(bit_tuple):
        ''' From tuple of occupied orbitals, return bitstring representation '''
        string = ['0']*(max(bit_tuple) + 1)
        for i in bit_tuple:
            string[i] = '1'
        string = ''.join(string[::-1])
        return BitArray(bin=string)


    def hamiltonian_matrix_element(self,det1,det2,Nint):
        """ return general Hamiltonian matrix element <det1|H|det2> """

        exc, degree, phase = get_excitation(det1,det2,Nint)

        if degree > 2:
            return 0

        elif degree == 2:
            # sign * <hole1,hole2||particle1,particle2>
            return phase * self.mol.double_bar[exc[1,0], exc[2,0], exc[1,1], exc[2,1]]

        elif degree == 1:
            m = exc[1,0]
            p = exc[1,1]
            common = common_index(det1,det2,Nint)
            tmp = self.mol.Hp[m,p]
            for n in common:
                tmp += self.mol.double_bar[m, n, p, n]
            return phase * tmp

        elif degree == 0:
            # kind of lazy to use common_index...
            common = common_index(det1,det2,Nint)
            tmp = 0.0
            for m in common:
                tmp += self.mol.Hp[m, m]
                for n in common:
                    tmp += 0.5*self.mol.double_bar[m,n,m,n]
            return phase * tmp

    def build_full_hamiltonian(self,det_list):
        ''' Given a list of determinants, construct the full Hamiltonian matrix '''

        Nint = int(np.floor(self.mol.norb/64) + 1)
        H = np.zeros((len(det_list),len(det_list)))

        print("Building Hamiltonian...")
        for idx,det1 in enumerate(det_list):
            for jdx,det2 in enumerate(det_list[:(idx+1)]):
               value = self.hamiltonian_matrix_element(det1,det2,Nint)
               H[idx,jdx] = value
               H[jdx,idx] = value

        return H

    def residues(self,determinant):
        ''' Returns list of residues, which is all possible ways to remove two
            electrons from a given determinant with number of orbitals nOrb
        '''
        nOrb = self.mol.norb
        residue_list = []
        nonzero = bin(determinant).count('1')
        for i in range(nOrb):
            mask1 = (1 << i) 
            for j in range(i):
                mask2 = (1 << j) 
                mask = mask1 ^ mask2
                if bin(determinant & ~mask).count('1') == (nonzero - 2):
                    residue_list.append(determinant & ~mask)
        return residue_list
    
    def add_particles(self,residue_list):
        ''' Returns list of determinants, which is all possible ways to add two
            electrons from a given residue_list with number of orbitals nOrb
        '''
        nOrb = self.mol.norb
        determinants = []
        for residue in residue_list:
            determinant = residue
            for i in range(nOrb):
                mask1 = (1 << i)
                if not bool(determinant & mask1):
                    one_particle = determinant | mask1
                    for j in range(i):
                        mask2 = (1 << j)
                        if not bool(one_particle & mask2):
                            two_particle = one_particle | mask2
                            determinants.append(two_particle)
        #return [format(det,'#0'+str(n_orbitals+2)+'b') for det in list(set(determinants))]
        return list(set(determinants))
    
    def single_and_double_determinants(self,determinant):
        return [np.array([i]) for i in self.add_particles(self.residues(determinant))]

    def CISD(self):
        ''' Do CISD from RHF reference '''

        nEle = self.mol.nelec
        reference_determinant = int(2**nEle - 1) # reference determinant, lowest nEle orbitals filled 
        det_list = self.single_and_double_determinants(reference_determinant)

        num_dets = len(det_list) 
        
        if num_dets > 5000:
            print("Number determinants: ", num_dets)
            sys.exit("CI too expensive. Quitting.")

        H = self.build_full_hamiltonian(det_list)

        print("Diagonalizing Hamiltonian...")
        #E,C = scipy.linalg.eigh(H)
        E,C = davidson(H,3)
        self.mol.ecisd = E[0] + self.mol.nuc_energy
        
        print("\nConfiguration Interaction Singles and Doubles")
        print("------------------------------")
        print("# Determinants: ",len(det_list))
        print("SCF energy:  %12.8f" % self.mol.energy.real)
        print("CISD corr:   %12.8f" % (self.mol.ecisd - self.mol.energy.real))
        print("CISD energy: %12.8f" % self.mol.ecisd)



    def FCI(self):
        """Routine to compute FCI energy from RHF reference"""

        nEle = self.mol.nelec
        nOrb = self.mol.norb
        det_list = []
 
        if comb(nOrb,nEle) > 5000:
            print("Number determinants: ",comb(nOrb,nEle))
            sys.exit("FCI too expensive. Quitting.")
         
        # FIXME: limited to 64 orbitals at the moment 
        for occlist in combinations(range(nOrb), nEle):
            string = PostSCF.tuple2bitstring(occlist)
            det = np.array([string.uint])
            det_list.append(det)

        H = self.build_full_hamiltonian(det_list)

        print("Diagonalizing Hamiltonian...")
        #E,C = scipy.linalg.eigh(H)
        E,C = davidson(H,3)
        self.mol.efci = E[0] + self.mol.nuc_energy
        
        print("\nFull Configuration Interaction")
        print("------------------------------")
        print("# Determinants: ",len(det_list))
        print("SCF energy: %12.8f" % self.mol.energy.real)
        print("FCI corr:   %12.8f" % (self.mol.efci - self.mol.energy.real))
        print("FCI energy: %12.8f" % self.mol.efci)

    def CIS(self):
        """  Routine to compute CIS from RHF reference """

        nOcc = self.mol.nelec
        nVir = self.mol.norb - self.mol.nelec
        nOV = nOcc * nVir 
        occ = slice(nOcc)
        vir = slice(nOcc,self.mol.norb)
 
        if nOV > 5000:
            print("Number determinants: ",nOV)
            sys.exit("CIS too expensive. Quitting.")

        A  = np.einsum('ab,ij->iajb',np.diag(np.diag(self.mol.fs)[vir]),np.diag(np.ones(nOcc))) # + e_a
        A -= np.einsum('ij,ab->iajb',np.diag(np.diag(self.mol.fs)[occ]),np.diag(np.ones(nVir))) # - e_i
        A += np.einsum('ajib->iajb',self.mol.double_bar[vir,occ,occ,vir]) # + <aj||ib>

        A = A.reshape(nOV,nOV)

        #if construction == 'bitstring':
        #    det_list = []
        #    # FIXME: limited to 64 orbitals at the moment 
        #    occ = range(nEle)
        #    vir = range(nEle,nOrb)
        #    occlist_string = product(combinations(occ,nEle-1),combinations(vir,1)) # all single excitations
        #    # FIXME: this will not work for Python < 3.5
        #    occlist_string = [(*a,*b) for a,b in occlist_string] # unpack tuples to list of tuples of occupied orbitals
        #    assert len(occlist_string) == nOV 
        #    for occlist in occlist_string: 
        #        string = PostSCF.tuple2bitstring(occlist)
        #        det = np.array([string.uint])
        #        det_list.append(det)
    
        #    A = self.build_full_hamiltonian(det_list)
        #    # subtract reference to get true "A" matrix
        #    A += np.eye(len(A))*(- self.mol.energy.real + self.mol.nuc_energy) 

 
        print("Diagonalizing Hamiltonian...")
        transition_energies, transition_densities = scipy.linalg.eigh(A)

        # MO tx dipole integrals
        spin = np.eye(2)
        mo_basis_dipoles = np.kron(np.einsum('uj,vi,...uv', \
                self.mol.C, self.mol.C, \
                self.mol.M).real,spin)
     
        oscillator_strengths = np.zeros_like(transition_energies) 
        for state in range(len(transition_energies)):
            transition_density = transition_densities[:,state]
            transition_dipoles = np.einsum('ia,pia->p', \
                transition_density.reshape(nOcc,nVir), \
                mo_basis_dipoles[:,occ,vir])
            sum_sq_td = np.einsum('p,p',transition_dipoles,transition_dipoles)
            oscillator_strengths[state] = (2/3)*transition_energies[state]*sum_sq_td
 

        # represent as energy differences / excitation energies
        transition_energies *= 27.211399 # to eV

        self.mol.cis_omega = transition_energies
        self.mol.cis_oscil = oscillator_strengths
        
        print("\nConfiguration Interaction Singles (CIS)")
        print("------------------------------")
        print("# Determinants: ",len(A))
        print("nOcc * nVirt:   ",nOV)
        for state in range(min(len(A),30)):
            print("CIS state %2s (eV): %12.4f (f=%6.4f)" % (state+1,self.mol.cis_omega[state],self.mol.cis_oscil[state]))


    def TDHF(self,alg='hermitian'):
        """  Routine to compute TDHF from RHF reference
    
             alg: 'hermitian' (does the Hermitian reduced variant, sqrt(A-B).(A+B).sqrt(A-B))
                  'reduced' (does the non-Hermitian reduced variant, (A-B).(A+B))
                  'full' (does the non-Hermitian [[A,B],[-B.T,-A.T]]')

        """

        nOcc = self.mol.nelec
        nVir = self.mol.norb - self.mol.nelec
        nOV = nOcc * nVir 
        occ = slice(nOcc)
        vir = slice(nOcc,self.mol.norb)

        # form full A and B matrices
        A  = np.einsum('ab,ij->iajb',np.diag(np.diag(self.mol.fs)[vir]),np.diag(np.ones(nOcc))) # + e_a 
        A -= np.einsum('ij,ab->iajb',np.diag(np.diag(self.mol.fs)[occ]),np.diag(np.ones(nVir))) # - e_i
        A += np.einsum('ajib->iajb',self.mol.double_bar[vir,occ,occ,vir]) # + <aj||ib>

        B  = np.einsum('abij->iajb',self.mol.double_bar[vir,vir,occ,occ]) # + <ab||ij>

        A = A.reshape(nOV,nOV)
        B = B.reshape(nOV,nOV)

        # doing Hermitian variant
        if alg == 'hermitian':
            sqrt_term = sqrtm(A-B) 
            H = np.dot(sqrt_term,np.dot(A+B,sqrt_term))
            transition_energies,transition_densities = scipy.linalg.eigh(H)
            transition_energies = np.sqrt(transition_energies)

        elif alg == 'reduced':
            H = np.dot(A-B,A+B)
            transition_energies,transition_densities = np.linalg.eig(H)
            transition_energies = np.sqrt(transition_energies)
            idx = transition_energies.argsort()
            transition_energies = transition_energies[idx].real

        elif alg == 'full':
            H = np.block([[A,B],[-B.T,-A.T]])
            transition_energies,transition_densities = np.linalg.eig(H)
            idx = transition_energies.argsort()
            transition_energies = transition_energies[idx].real
            # take positive eigenvalues
            transition_energies = transition_energies[nOV:]

        transition_energies *= 27.211399 # to eV
        self.mol.tdhf_omega = transition_energies
        
        print("\nTime-dependent Hartree-Fock (TDHF)")
        print("------------------------------")
        print("Algorithm:        ",alg)
        print("Matrix shape:     ",len(H))
        print("2 * nOcc * nVirt: ",2*nOV)
        for state in range(min(len(A),10)):
            print("TDHF state %2s (eV): %12.4f" % (state+1,self.mol.tdhf_omega[state]))














