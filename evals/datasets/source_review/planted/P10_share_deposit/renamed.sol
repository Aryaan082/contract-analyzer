// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ID1 {
    uint256 public ID2;
    uint256 public ID3;
    mapping(address => uint256) public ID4;

    function ID5(uint256 ID6) external returns (uint256 ID7) {
        require(ID6 > 0, "zero");
        ID7 = ID3 == 0
            ? ID6
            : (ID6 * ID3 + ID2 - 1) / ID2;
        ID2 += ID6;
        ID3 += ID7;
        ID4[msg.sender] += ID7;
    }

    function ID8(uint256 ID7) external returns (uint256 ID6) {
        ID4[msg.sender] -= ID7;
        ID6 = ID7 * ID2 / ID3;
        ID3 -= ID7;
        ID2 -= ID6;
    }
}
