import "geometry"
import "math"

let points = [geometry.Vec2 { x: 0.0, y: 0.0 }, geometry.Vec2 { x: 4.0, y: 0.0 }, geometry.Vec2 { x: 2.0, y: 6.0 }]
if let c = geometry.centroid(points) {
    print("centroid:", c, "length", math.clamp(c.length(), 0.0, 3.0))
}
print("no points:", geometry.centroid([]))
print("gcd from the standard library:", math.gcd(48, 180))
