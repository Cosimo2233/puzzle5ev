# Source this file in an interactive shell: . /opt/ec26e/env.sh
export PATH=/opt/ec26e/venv-python311/bin:$PATH
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export EC_PREPROCESS_BACKEND=cpu
export EC_RIGID_BACKEND=cpu
export EC_CLEARANCE_BACKEND=cpu
export EC_PL_ACCESS=linux
# DISPLAY is set explicitly for local GUI commands, e.g. DISPLAY=:0 python ...
