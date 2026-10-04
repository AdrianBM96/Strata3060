"""Fibonacci iterativo."""


def fib(n):
    """Devuelve el n-esimo numero de Fibonacci (fib(0)=0, fib(1)=1)."""
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


if __name__ == "__main__":
    print(fib(10))
