"""Create the included L bracket from a triangulated profile with two holes."""
from pathlib import Path
import numpy as np
from scipy.spatial import Delaunay
from backend.geometry import write_binary_stl, read_stl


def make_example():
    arc = np.column_stack([19+5*np.cos(np.linspace(-np.pi/2,-np.pi,9)),13+5*np.sin(np.linspace(-np.pi/2,-np.pi,9))])
    outer = np.vstack([[-14,0],[65,0],[65,8],arc,[14,50],[-14,50]])
    theta = np.linspace(0,2*np.pi,33)[:-1]
    holes = [np.column_stack([3.5*np.cos(theta),z+3.5*np.sin(theta)]) for z in (18,39)]
    points = np.vstack([outer,*holes])
    faces = Delaunay(points).simplices
    centers = points[faces].mean(axis=1)
    def contains(polygon):
        inside = np.zeros(len(centers),dtype=bool)
        for a,b in zip(polygon,np.roll(polygon,-1,axis=0)):
            if abs(b[1]-a[1]) < 1e-12:
                continue
            crossing = ((a[1]>centers[:,1]) != (b[1]>centers[:,1])) & (centers[:,0] < (b[0]-a[0])*(centers[:,1]-a[1])/(b[1]-a[1])+a[0])
            inside ^= crossing
        return inside
    inside = contains(outer)
    for hole in holes:
        inside &= ~contains(hole)
    faces = faces[inside]
    layers = [np.column_stack([points[:,0],np.full(len(points),y),points[:,1]]) for y in (-8,8)]
    triangles = []
    # Delaunay has CCW faces in x/z. That normal points towards -Y.
    triangles.extend(layers[0][faces])
    triangles.extend(layers[1][faces[:,::-1]])
    for boundary in [outer,*[hole[::-1] for hole in holes]]:
        # Use coordinates directly so reversed hole indexing stays simple.
        a = np.column_stack([boundary[:,0],np.full(len(boundary),-8),boundary[:,1]])
        b = a.copy(); b[:,1] = 8
        for i in range(len(boundary)):
            j = (i+1)%len(boundary)
            triangles.extend([[a[i],b[i],b[j]],[a[i],b[j],a[j]]])
    data = write_binary_stl(np.array(triangles))
    mesh = read_stl(data)
    if not mesh.is_watertight:
        raise RuntimeError('Example surface is not closed')
    target = Path(__file__).resolve().parents[1]/'backend/data/backpack-bracket.stl'
    target.write_bytes(data)
    print(f'{len(mesh.faces)} triangles; closed; {mesh.extents} mm')


if __name__ == '__main__':
    make_example()
