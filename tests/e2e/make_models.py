"""Write small binary STL test models: a 20 mm cube and a 15 mm x 30 mm cylinder."""
import math, struct, sys, os

def write_stl(path, tris):
    with open(path, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(tris)))
        for a, b, c in tris:
            f.write(struct.pack("<12fH", 0, 0, 0, *a, *b, *c, 0))

def cube(s):
    v = [(x, y, z) for x in (0, s) for y in (0, s) for z in (0, s)]
    faces = [(0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3)]
    tris = []
    for a,b,c,d in faces:
        tris += [(v[a],v[b],v[c]),(v[a],v[c],v[d])]
    return tris

def cylinder(r, h, n=96):
    tris = []
    for i in range(n):
        a0, a1 = 2*math.pi*i/n, 2*math.pi*(i+1)/n
        p0 = (r*math.cos(a0), r*math.sin(a0)); p1 = (r*math.cos(a1), r*math.sin(a1))
        tris.append(((0,0,0),(p1[0],p1[1],0),(p0[0],p0[1],0)))
        tris.append(((0,0,h),(p0[0],p0[1],h),(p1[0],p1[1],h)))
        tris.append(((p0[0],p0[1],0),(p1[0],p1[1],0),(p1[0],p1[1],h)))
        tris.append(((p0[0],p0[1],0),(p1[0],p1[1],h),(p0[0],p0[1],h)))
    return tris

out = sys.argv[1]
write_stl(os.path.join(out, "cube20.stl"), cube(20))
write_stl(os.path.join(out, "cylinder15.stl"), cylinder(7.5, 30))
print("models written")
