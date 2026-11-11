# Structs, enums, methods and exhaustive matching.
struct Point { x: Float, y: Float }

impl Point {
    fn distance(self, other: Point) -> Float {
        let dx = self.x - other.x
        let dy = self.y - other.y
        sqrt(dx * dx + dy * dy)
    }
}

enum Shape {
    Circle(Point, Float)          # center, radius
    Rect(Point, Point)            # two corners
    Polygon(List[Point])
}

impl Shape {
    fn area(self) -> Float {
        match self {
            Circle(_, r) => 3.141592653589793 * r * r
            Rect(a, b) => ((b.x - a.x) * (b.y - a.y)).abs()
            Polygon(points) => {
                # The shoelace formula.
                var sum = 0.0
                for i in 0..points.len() {
                    let p = points[i]
                    let q = points[(i + 1) % points.len()]
                    sum += p.x * q.y - q.x * p.y
                }
                sum.abs() / 2.0
            }
        }
    }

    fn name(self) -> Str {
        match self {
            Circle(_, _) => "circle"
            Rect(_, _) => "rectangle"
            Polygon(points) => "${points.len()}-gon"
        }
    }
}

let origin = Point { x: 0.0, y: 0.0 }
let shapes = [
    Shape.Circle(origin, 1.0),
    Shape.Rect(Point { x: 1.0, y: 1.0 }, Point { x: 4.0, y: 3.0 }),
    Shape.Polygon([origin, Point { x: 4.0, y: 0.0 }, Point { x: 0.0, y: 3.0 }]),
]

for s in shapes.sort_by(fn(s) { s.area() }) {
    print("${s.name()}: area ${float(round(s.area() * 100.0)) / 100.0}")
}
let total = shapes.map(fn(s) { s.area() }).sum()
print("total area is about", round(total))
print("distance:", origin.distance(Point { x: 3.0, y: 4.0 }))
