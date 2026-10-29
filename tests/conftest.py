import os
import sys

# 시험이 tools/ 의 모듈을 바로 부르게 한다(폴더만 떼어 가도 돌게)
TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
