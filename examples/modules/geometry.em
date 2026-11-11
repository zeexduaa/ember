# A module: only `pub` declarations are visible to files that import it.
pub struct Vec2 { x: Float, y: Float }

impl Vec2 {
    fn add(self, o: Vec2) -> Vec2 { Vec2 { x: self.x + o.x, y: self.y + o.y } }
    fn scale(self, k: Float) -> Vec2 { Vec2 { x: self.x * k, y: self.y * k } }
    fn length(self) -> Float { sqrt(self.x * self.x + self.y * self.y) }
    fn zero() -> Vec2 { Vec2 { x: 0.0, y: 0.0 } }
}

pub fn centroid(points: List[Vec2]) -> Vec2? {
    if points.is_empty() { return nil }
    points.reduce(Vec2.zero(), fn(acc, p) { acc.add(p) }).scale(1.0 / float(points.len()))
}

fn helper() -> Int { 1 }     # private: importers can't see this
