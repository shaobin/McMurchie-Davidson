from mmd.molecule import * 
from mmd.scf import * 

water = """
0 1
O    0.000000      -0.075791844    0.000000
H    0.866811829    0.601435779    0.000000
H   -0.866811829    0.601435779    0.000000
"""

# init molecule and build integrals
mol = Molecule(geometry=water,basis='sto-3g')
mol.build()

# do the SCF
scf = SCF(mol)
scf.RHF()



