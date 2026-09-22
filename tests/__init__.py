"""Paquete de tests.

Lo único que hace este archivo es poner la reja de red ANTES de que se
importe cualquier test: los tests no tocan servicios reales. Ver
`tests/sin_red.py` para el porqué y para qué parchear cuando salta.

Va acá y no en un helper que cada archivo tenga que acordarse de llamar,
justamente porque el problema es olvidarse: `unittest discover tests` y
`python3 -m unittest tests.test_secretaria` importan los dos este
paquete, así que no hay forma de correr un test de este proyecto sin la
reja puesta.
"""
from . import sin_red

sin_red.cortar()
