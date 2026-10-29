// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface ID1 {
    function ID2() external view returns (uint112, uint112, uint32);
}

contract ID3 {
    ID1 public immutable ID4;
    mapping(address => uint256) public ID5;
    mapping(address => uint256) public ID6;

    constructor(ID1 ID7) {
        ID4 = ID7;
    }

    function ID8(uint256 ID9) external {
        ID5[msg.sender] += ID9;
    }

    function ID10(uint256 ID9) external {
        (uint112 ID11, uint112 ID12, ) = ID4.ID2();
        uint256 value = ID5[msg.sender] * uint256(ID12) / uint256(ID11);
        require((ID6[msg.sender] + ID9) * 3 <= value * 2, "undercollateralised");
        ID6[msg.sender] += ID9;
    }
}
