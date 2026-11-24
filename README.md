# 研发证据转化追踪与产品传播口径复核

合成实验方案、原料批次、配方产品版本和传播表述审批样例。

All example identifiers and values are synthetic. CSV files use UTF-8. The first row contains column names, and an empty cell means the source supplied no value.

## Sample files

- `studies.csv` contains 科研产出和测试条件及结果。 Columns are `study`, `material`, `concentration`, `condition`, `result`, `version`.
- `products.csv` contains 原料批次、配方版本及产品批次关联。 Columns are `product`, `formula`, `material`, `batch`, `from`, `to`.
- `claims.csv` contains 经审批的产品表述、引用证据和渠道有效期。 Columns are `claim`, `product`, `formula`, `study`, `channel`, `valid_to`, `state`.

Python 3 standard library is sufficient to inspect these inputs. Run `python3 -m unittest discover -s tests -v` to check the delivered file structures and example references.
