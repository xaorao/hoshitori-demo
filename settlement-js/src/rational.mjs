// Exact arithmetic on decimal prices: a number is a fraction of two BigInts, so nothing is ever rounded unless a rule says to.

export class Invalid extends Error {}

const PLAIN = /^-?(0|[1-9][0-9]*)(\.[0-9]+)?$/;

export class Q {
  constructor(num, den = 1n) {
    if (den === 0n) throw new Invalid("a denominator of zero");
    if (den < 0n) {
      num = -num;
      den = -den;
    }
    this.num = num;
    this.den = den;
  }

  static parse(text) {
    if (typeof text !== "string" || !PLAIN.test(text)) throw new Invalid(`not a plain decimal: ${text}`);
    const [whole, fraction = ""] = text.replace("-", "").split(".");
    const magnitude = BigInt(whole + fraction);
    return new Q(text.startsWith("-") ? -magnitude : magnitude, 10n ** BigInt(fraction.length));
  }

  static of(integer) {
    return new Q(BigInt(integer));
  }

  cmp(other) {
    const left = this.num * other.den;
    const right = other.num * this.den;
    return left < right ? -1 : left > right ? 1 : 0;
  }

  add(other) {
    return new Q(this.num * other.den + other.num * this.den, this.den * other.den);
  }

  sub(other) {
    return new Q(this.num * other.den - other.num * this.den, this.den * other.den);
  }

  mul(other) {
    return new Q(this.num * other.num, this.den * other.den);
  }

  div(other) {
    return new Q(this.num * other.den, this.den * other.num);
  }

  abs() {
    return new Q(this.num < 0n ? -this.num : this.num, this.den);
  }

  isPositive() {
    return this.num > 0n;
  }

  // The nearest whole number, halves to the even neighbour.
  roundHalfEven() {
    let quotient = this.num / this.den;
    let remainder = this.num % this.den;
    if (remainder < 0n) {
      quotient -= 1n;
      remainder += this.den;
    }
    const twice = remainder * 2n;
    if (twice > this.den || (twice === this.den && quotient % 2n !== 0n)) quotient += 1n;
    return quotient;
  }
}
